import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from packet_foundry_contracts import ArtifactRef, ExperimentMetadata, Material, SpecificationRef
from packet_foundry_factory.config import TracingConfig
from packet_foundry_factory.tracing import Tracer
from test_providers import assignment


async def test_optional_trace_content_redaction_and_start_finish_order(monkeypatch):
    calls = []
    class Client:
        def __init__(self, **_):
            pass
        def create_run(self, **kwargs):
            calls.append(("start", kwargs))
        def update_run(self, **kwargs):
            calls.append(("finish", kwargs))
        def create_project(self, **kwargs):
            calls.append(("project", kwargs))
    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(Client=Client))
    monkeypatch.setenv("LANGSMITH_API_KEY", "fixture")
    trace = Tracer(TracingConfig(enabled=True, capture_content=False))
    job = assignment()
    job.packet.batch_id = "batch"
    job.packet.specifications = [SpecificationRef(spec_id="brand", version="v1", sha256="a" * 64)]
    job.packet.experiment = ExperimentMetadata(dataset_id=str(uuid4()), case_id=str(uuid4()))
    await trace.start(job)
    await trace.finish(job, {"result": {"text": "private output"}})
    await trace.close()
    assert [kind for kind, _ in calls] == ["start", "finish"]
    assert calls[0][1]["inputs"] == {"packet_id": job.packet.packet_id}
    assert calls[1][1]["outputs"] == {"completed": True}
    assert calls[0][1]["project_name"] == "packet-foundry"
    assert calls[0][1]["extra"]["metadata"]["case_id"] == job.packet.experiment.case_id
    assert calls[0][1]["extra"]["metadata"]["specifications"] == [
        {"spec_id": "brand", "version": "v1", "sha256": "a" * 64}]
    assert "reference_example_id" not in calls[0][1]


async def test_trace_export_failure_does_not_fail_generation(monkeypatch):
    class Client:
        def __init__(self, **_):
            pass
        def create_run(self, **_):
            raise OSError("no network")
        def update_run(self, **_):
            raise OSError("no network")
    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(Client=Client))
    monkeypatch.setenv("LANGSMITH_API_KEY", "fixture")
    trace = Tracer(TracingConfig(enabled=True))
    job = assignment()
    await trace.start(job)
    await trace.fail(job, "provider_timeout")
    await trace.close()
    assert not trace.runs


async def test_trace_disabled_has_no_dependency_or_network():
    trace = Tracer(TracingConfig())
    await trace.start(assignment())
    await trace.finish(assignment(), {"result": {}})
    await trace.close()
    assert trace.client is None and not trace.tasks


def test_managed_startup_tracing_credential_file(tmp_path, monkeypatch):
    received = {}
    class Client:
        def __init__(self, **kwargs):
            received.update(kwargs)
    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(Client=Client))
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    path = tmp_path / "tracing.key"
    path.write_text("private-fixture-key\n", encoding="utf-8")
    config = TracingConfig(enabled=True, api_key_env=None, api_key_file=path,
                           endpoint="https://tracing.example.test")
    assert "private-fixture-key" not in config.model_dump_json()
    assert Tracer(config).client is not None
    assert received["api_key"] == "private-fixture-key"
    assert received["api_url"] == config.endpoint
    path.unlink()
    assert Tracer(config).client is None
    with pytest.raises(ValueError, match="choose"):
        TracingConfig(enabled=True, api_key_file=path)


async def test_media_attachments_use_sdk_bytes_contract_with_explicit_capture(tmp_path, monkeypatch):
    calls = []
    class Client:
        def __init__(self, **_):
            pass
        def create_run(self, **kwargs):
            calls.append(("start", kwargs))
        def update_run(self, **kwargs):
            calls.append(("finish", kwargs))
    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(Client=Client))
    monkeypatch.setenv("LANGSMITH_API_KEY", "fixture")
    source, output = tmp_path / "source.png", tmp_path / "generated.wav"
    source.write_bytes(b"input-image")
    output.write_bytes(b"output-audio")
    job = assignment()
    job.packet.materials = [Material(kind="image", name="reference",
        artifact=ArtifactRef(artifact_id="input", sha256="a" * 64, size_bytes=11, mime_type="image/png"))]
    trace = Tracer(TracingConfig(enabled=True, capture_content=True))
    trace_id = await trace.start(job, {"reference": source})
    await trace.finish(job, {"result": {"metadata": {"langsmith_trace_id": trace_id}},
        "files": [{"path": str(output), "mime_type": "audio/wav"}]})
    await trace.close()
    assert calls[0][1]["attachments"] == {"input_0_image": ("image/png", b"input-image")}
    assert calls[1][1]["attachments"] == {"output_0": ("audio/wav", b"output-audio")}
    assert calls[1][1]["outputs"]["metadata"]["langsmith_trace_id"] == trace_id


def test_attachment_budget_omits_large_files_without_losing_other_outputs(tmp_path):
    path = tmp_path / "large.mp4"
    path.write_bytes(b"too large")
    trace = Tracer(TracingConfig(capture_content=True, max_attachment_bytes=2))
    assert trace.attachments([("output", "video/mp4", path)]) == {}


async def test_actual_optional_sdk_serializes_attachments_to_multipart(tmp_path, monkeypatch):
    """Exercise real SDK serialization with an in-memory HTTP session, never a live API."""
    langsmith = pytest.importorskip("langsmith")
    import requests
    real_client = langsmith.Client
    calls = []
    class Session(requests.Session):
        def request(self, method, url, **kwargs):
            data = kwargs.get("data", b"")
            if hasattr(data, "read"):
                data = data.read()
            if isinstance(data, str):
                data = data.encode()
            kwargs["serialized_body"] = data
            calls.append((method, url, kwargs))
            response = requests.Response()
            response.status_code = 200
            response.url = url
            response._content = b'{}'
            response.headers["Content-Type"] = "application/json"
            return response
    client = real_client(api_url="https://langsmith.test", api_key="fixture-not-real", session=Session(),
                         info={})
    monkeypatch.setattr(langsmith, "Client", lambda **_: client)
    monkeypatch.setenv("LANGSMITH_API_KEY", "fixture-not-real")
    trace = Tracer(TracingConfig(enabled=True, capture_content=True))
    path = tmp_path / "audio.wav"
    path.write_bytes(b"synthetic-audio-payload")
    job = assignment()
    await trace.start(job)
    await trace.finish(job, {"result": {"text": "fixture"},
        "files": [{"path": str(path), "mime_type": "audio/wav"}]})
    await trace.close()
    assert any("multipart" in url for _, url, _ in calls), calls
    bodies = b"".join(kwargs["serialized_body"] for _, _, kwargs in calls)
    assert b"synthetic-audio-payload" in bodies and b"audio/wav" in bodies
    # Real SDK serialization, not only a fake callback, carries the bytes to multipart ingest.
