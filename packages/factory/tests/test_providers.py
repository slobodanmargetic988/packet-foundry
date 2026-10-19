from __future__ import annotations

import base64
import json
import os
import sys
from datetime import timedelta

import httpx
import pytest
from packet_foundry_contracts import (
    Assignment,
    ExecutionProfile,
    GenerationRequest,
    Material,
    OutputSpec,
    Packet,
    utcnow,
)
from packet_foundry_factory.config import BackendConfig, WorkflowTemplate
from packet_foundry_factory.providers import (
    Provider,
    ProviderError,
    cli_generate,
    native_executable,
    run_process,
    safe_comfy_file,
    text_output,
)


def assignment(kind="text", backend="test", *, parameters=None, template=None):
    return Assignment(packet=Packet(application_id="test", idempotency_key="one",
        request=GenerationRequest(instructions="Write a greeting", parameters=parameters or {},
                                  template=template), output=OutputSpec(kind=kind)),
        attempt_id="attempt-1", factory_id="factory-1", fencing_token=1,
        lease_expires_at=utcnow() + timedelta(seconds=120),
        profile=ExecutionProfile(profile_id="profile", model="configured-model", backend=backend,
                                  output_kinds=[kind]), resolved_instructions="Write a greeting")


async def no_operation(_):
    pytest.fail("Synchronous provider must not record an asynchronous operation")


@pytest.mark.parametrize("backend", ["openai", "anthropic", "ollama", "openai_compatible"])
async def test_text_and_json_contracts_use_no_tools(tmp_path, backend):
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        if backend == "openai":
            data = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"answer":42}'}]}]}
        elif backend == "anthropic":
            data = {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"answer":42}'}]}
        elif backend == "ollama":
            data = {"message": {"content": '{"answer":42}'}}
        else:
            data = {"choices": [{"finish_reason": "stop", "message": {"content": '{"answer":42}'}}]}
        return httpx.Response(200, json=data)
    provider = Provider(BackendConfig(kind=backend, auth_mode="none", base_url="https://provider.test/v1"),
                        1000, httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    job = assignment("json")
    job.resolved_output_schema = {"type": "object", "properties": {"answer": {"type": "integer"}},
                                  "required": ["answer"]}
    result = await provider.generate(job, tmp_path, {}, no_operation)
    assert result["result"]["json_data"] == {"answer": 42}
    assert seen[0]["model"] == "configured-model"
    assert not seen[0].get("tools")
    await provider.client.aclose()


def test_invalid_json_is_never_success():
    with pytest.raises(ProviderError, match="JSON/schema"):
        text_output(assignment("json"), "```json\n{}\n```")


async def test_packet_cannot_inject_tools_or_endpoints(tmp_path):
    provider = Provider(BackendConfig(kind="openai", auth_mode="none"), 1000,
                        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("no request"))))
    with pytest.raises(ProviderError, match="Unsupported generation parameters"):
        await provider.generate(assignment(parameters={"tools": [{"type": "web_search"}]}), tmp_path, {}, no_operation)
    await provider.client.aclose()


async def test_gemini_image_extracts_binary(tmp_path):
    image = b"\x89PNG\r\n\x1a\nfixture"
    provider = Provider(BackendConfig(kind="gemini_image", auth_mode="none"), 1000,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "image/png", "data": base64.b64encode(image).decode()}}]}}]}))))
    result = await provider.generate(assignment("image"), tmp_path, {}, no_operation)
    assert (tmp_path / "output.png").read_bytes() == image
    assert result["files"][0]["mime_type"] == "image/png"
    await provider.client.aclose()


async def test_veo_operation_is_recorded_before_poll_and_resumes(tmp_path):
    recorded, calls = [], []
    async def save(value):
        recorded.append(value)
    def handle(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"name": "operations/video-1"})
        assert recorded, "Must persist the operation before polling"
        return httpx.Response(200, json={"done": True, "response": {"generatedVideos": [
            {"video": {"data": base64.b64encode(b"video").decode()}}]}})
    provider = Provider(BackendConfig(kind="veo", auth_mode="none"), 1000,
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    result = await provider.generate(assignment("video"), tmp_path, {}, save)
    assert result["files"] and calls == ["POST", "GET"]
    calls.clear()
    await provider.generate(assignment("video"), tmp_path, {}, save, recorded[0])
    assert calls == ["GET"]
    await provider.client.aclose()


async def test_veo_rejects_external_artifact_host_without_sending_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_TEST_KEY", "secret-fixture")
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"done": True, "response": {"generatedVideos": [
            {"video": {"uri": "https://untrusted.invalid/steal"}}]}})
    provider = Provider(BackendConfig(kind="veo", api_key_env="GOOGLE_TEST_KEY"), 1000,
                        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    with pytest.raises(ProviderError, match="outside configured"):
        await provider.generate(assignment("video"), tmp_path, {}, no_operation,
                                {"name": "operations/video-1"})
    assert len(calls) == 1
    await provider.client.aclose()


async def test_speech_returns_file_not_playback(tmp_path):
    provider = Provider(BackendConfig(kind="openai_speech", auth_mode="none"), 1000,
                        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"audio"))))
    result = await provider.generate(assignment("audio"), tmp_path, {}, no_operation)
    assert (tmp_path / "output.mp3").read_bytes() == b"audio"
    assert result["files"][0]["mime_type"] == "audio/mpeg"
    await provider.client.aclose()


async def test_comfyui_approved_mapping_and_output_nodes(tmp_path):
    calls, recorded = [], []
    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/prompt":
            workflow = json.loads(request.content)["prompt"]
            assert workflow["1"]["inputs"]["text"] == "Write a greeting"
            assert workflow["1"]["class_type"] == "ApprovedNode"
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            assert recorded
            return httpx.Response(200, json={"prompt-1": {"status": {"completed": True}, "outputs": {
                "2": {"images": [{"filename": "output.png", "subfolder": "", "type": "output"}]},
                "unexpected": {"images": [{"filename": "private.png"}]}}}})
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "output.png"
        return httpx.Response(200, content=b"image")
    config = BackendConfig(kind="comfyui", auth_mode="none", base_url="http://127.0.0.1:8188",
        templates={"approved": WorkflowTemplate(workflow={"1": {"class_type": "ApprovedNode", "inputs": {"text": ""}}},
            mappings=[{"node": "1", "input": "text", "source": "instructions"}], output_nodes=["2"])})
    provider = Provider(config, 1000, httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    async def save(value):
        recorded.append(value)
    result = await provider.generate(assignment("image", template="approved"), tmp_path, {}, save)
    assert len(result["files"]) == 1
    calls.clear()
    await provider.generate(assignment("image", template="approved"), tmp_path, {}, save, recorded[0])
    assert "/prompt" not in calls
    await provider.client.aclose()


async def test_comfyui_full_prompt_preserves_shared_rules_and_legacy_mappings(tmp_path):
    submitted = []
    def handle(request):
        if request.url.path == "/prompt":
            graph = json.loads(request.content)["prompt"]
            submitted.append(graph)
            return httpx.Response(200, json={"prompt_id": "combined-prompt"})
        if request.url.path == "/history/combined-prompt":
            return httpx.Response(200, json={"combined-prompt": {"status": {"completed": True},
                "outputs": {"9": {"images": [{"filename": "output.png", "type": "output"}]}}}})
        assert request.url.path == "/view"
        return httpx.Response(200, content=b"synthetic-image")
    config = BackendConfig(kind="comfyui", auth_mode="none", templates={
        "approved": WorkflowTemplate(workflow={str(i): {"class_type": "ApprovedTextNode",
            "inputs": {"text": ""}} for i in [1, 2, 3]}, mappings=[
                {"node": "1", "input": "text", "source": "prompt"},
                {"node": "2", "input": "text", "source": "instructions"},
                {"node": "3", "input": "text", "source": "system_prompt"}], output_nodes=["9"])})
    provider = Provider(config, 1000, httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    job = assignment("image", template="approved")
    job.resolved_system_prompt = "Use brand blue and include no text."
    job.packet.materials = [Material(kind="text", name="brief", text="Friendly geometric shapes.")]
    async def save(_):
        pass
    await provider.generate(job, tmp_path, {}, save)
    source_text = "Write a greeting\n\nSource brief:\nFriendly geometric shapes."
    assert submitted[0]["1"]["inputs"]["text"] == job.resolved_system_prompt + "\n\n" + source_text
    assert submitted[0]["2"]["inputs"]["text"] == source_text
    assert submitted[0]["3"]["inputs"]["text"] == job.resolved_system_prompt
    job.resolved_system_prompt = "   "
    await provider.generate(job, tmp_path, {}, save)
    assert submitted[1]["1"]["inputs"]["text"] == source_text
    await provider.client.aclose()


@pytest.mark.parametrize("name,folder", [("../secret", ""), ("secret", "../"),
    ("C:\\secret", ""), ("secret", "/root"), ("secret", "C:\\root")])
def test_comfyui_rejects_traversal(name, folder):
    with pytest.raises(ProviderError):
        safe_comfy_file(name, folder)


@pytest.mark.parametrize("kind", ["claude", "codex"])
async def test_cli_policy_and_local_subscription_auth(tmp_path, monkeypatch, kind):
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://unexpected-provider.invalid")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    async def fake_run(command, prompt, cwd, env, timeout):
        captured.update(command=command, prompt=prompt, cwd=cwd, env=env)
        return (0, json.dumps({"type": "result", "result": "hello", "num_turns": 1})) if kind == "claude" else (
            0, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hello"}}))
    monkeypatch.setattr("packet_foundry_factory.providers.run_process", fake_run)
    result = await cli_generate(BackendConfig(kind=kind, auth_mode="subscription"), assignment(), tmp_path)
    assert result["result"]["text"] == "hello"
    assert "OPENAI_API_KEY" not in captured["env"] and "ANTHROPIC_API_KEY" not in captured["env"]
    assert "ANTHROPIC_BASE_URL" not in captured["env"] and "CLAUDE_CODE_USE_BEDROCK" not in captured["env"]
    assert captured["cwd"] == tmp_path / "isolated"
    if kind == "claude":
        assert captured["command"][captured["command"].index("--tools") + 1] == ""
        assert "--max-turns" in captured["command"]
    else:
        assert "--ignore-user-config" in captured["command"]
        assert any("model_instructions_file=" in value for value in captured["command"])
        assert "features.shell_tool=false" in captured["command"]


async def test_subprocess_input_is_literal_and_timeout_reaps(tmp_path):
    prompt = "literal `commands` $(echo no) $SECRET"
    code, result = await run_process([sys.executable, "-c", "import sys;print(sys.stdin.read())"],
                                      prompt, tmp_path, os.environ.copy(), 10)
    assert code == 0 and result.strip() == prompt
    with pytest.raises(TimeoutError):
        await run_process([sys.executable, "-c", "import time;time.sleep(30)"], "", tmp_path,
                          os.environ.copy(), 0.05)


async def test_provider_errors_do_not_echo_secrets(tmp_path):
    provider = Provider(BackendConfig(kind="openai", auth_mode="none"), 1000,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401, text="secret-key"))))
    with pytest.raises(ProviderError) as caught:
        await provider.generate(assignment(), tmp_path, {}, no_operation)
    assert "secret-key" not in str(caught.value)
    await provider.client.aclose()


async def test_claude_event_array_compatibility_and_subscription_policy(tmp_path, monkeypatch):
    events = [{"type": "system", "subtype": "init", "tools": []},
              {"type": "result", "result": "hello", "num_turns": 1, "is_error": False}]
    async def fake_run(*_):
        return 0, json.dumps(events)
    monkeypatch.setattr("packet_foundry_factory.providers.run_process", fake_run)
    config = BackendConfig(kind="claude", auth_mode="subscription")
    result = await cli_generate(config, assignment(), tmp_path)
    assert result["result"]["text"] == "hello"
    events[-1].update(is_error=True, result="Your organization has disabled Claude subscription access for Claude Code")
    with pytest.raises(ProviderError) as caught:
        await cli_generate(config, assignment(), tmp_path)
    assert caught.value.code == "cli_access_denied"


async def test_claude_rejects_unexpected_tool_event(tmp_path, monkeypatch):
    async def fake_run(*_):
        return 0, json.dumps([{"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
                              {"type": "result", "result": "hello", "num_turns": 1}])
    monkeypatch.setattr("packet_foundry_factory.providers.run_process", fake_run)
    with pytest.raises(ProviderError) as caught:
        await cli_generate(BackendConfig(kind="claude", auth_mode="subscription"), assignment(), tmp_path)
    assert caught.value.code == "unexpected_tool_call"


def test_windows_batch_shims_are_rejected_without_implicit_shell():
    with pytest.raises(ProviderError) as caught:
        native_executable("C:\\npm\\codex.cmd", platform="nt")
    assert caught.value.code == "cli_not_native"
