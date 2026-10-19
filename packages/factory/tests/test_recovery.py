from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta

import httpx
import pytest
from packet_foundry_contracts import (
    ArtifactRef,
    Assignment,
    Completion,
    ExecutionProfile,
    FactoryRegistration,
    GenerationRequest,
    Packet,
    ResultPackage,
    utcnow,
)
from packet_foundry_factory.config import BackendConfig, FactoryConfig
from packet_foundry_factory.daemon import FactoryDaemon, InstanceLock
from packet_foundry_factory.journal import Journal
from packet_foundry_factory.protocol import RegistryClient
from packet_foundry_factory.providers import artifact_output, text_output


@pytest.fixture
def job():
    return Assignment(packet=Packet(application_id="test", idempotency_key="one",
        request=GenerationRequest(instructions="Write a greeting")),
        attempt_id="attempt-1", factory_id="factory-1", fencing_token=1,
        lease_expires_at=utcnow() + timedelta(seconds=120),
        profile=ExecutionProfile(profile_id="profile", model="model", backend="local"),
        resolved_instructions="Write a greeting")


@pytest.fixture
def config(tmp_path, job):
    return FactoryConfig(registry_url="http://registry.test", data_dir=tmp_path,
        registration=FactoryRegistration(factory_id=job.factory_id, name="test", profiles=[job.profile]),
        backends={"local": BackendConfig(kind="ollama", auth_mode="none")},
        renew_seconds=0.01, heartbeat_seconds=0.01, reconnect_seconds=0.01)


class Registry:
    def __init__(self):
        self.completed, self.failed = [], []
        self.renewals = self.beats = self.registrations = self.uploads = self.currents = 0
    async def register(self):
        self.registrations += 1
    async def heartbeat(self):
        self.beats += 1
    async def work(self):
        await asyncio.sleep(0.05)
        return None
    async def current(self, job):
        self.currents += 1
        return job
    async def renew(self, job):
        self.renewals += 1
        return (utcnow() + timedelta(seconds=120)).isoformat()
    async def complete(self, job, completion):
        self.completed.append(completion)
    async def fail(self, job, failure):
        self.failed.append(failure)
    async def upload(self, job, path, mime):
        self.uploads += 1
        return ArtifactRef(artifact_id="output", sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           size_bytes=path.stat().st_size, mime_type=mime)
    async def close(self):
        pass


class EchoProvider:
    calls = 0
    active = 0
    maximum = 0
    def __init__(self, *_):
        self.client = httpx.AsyncClient()
    async def generate(self, job, directory, inputs, save_operation, operation):
        type(self).calls += 1
        type(self).active += 1
        type(self).maximum = max(type(self).maximum, type(self).active)
        try:
            await asyncio.sleep(0.08)
            return text_output(job, "hello")
        finally:
            type(self).active -= 1


async def close(daemon):
    await daemon.registry.close()
    await daemon.tracer.close()
    daemon.journal.close()


async def test_renewal_and_heartbeat_run_while_inference_is_busy(config, job):
    registry = Registry()
    daemon = FactoryDaemon(config, registry=registry, provider_factory=EchoProvider)
    daemon.journal.receive(job)
    heartbeat = asyncio.create_task(daemon.heartbeats())
    await daemon.execute(job, recovery=False)
    daemon.stop.set()
    await heartbeat
    assert registry.renewals >= 2 and registry.beats >= 2
    assert registry.completed[0].result.text == "hello"
    assert daemon.journal.get(job.attempt_id)["state"] == "acknowledged"
    await close(daemon)


async def test_completion_replay_never_reexecutes_provider(config, job):
    registry = Registry()
    daemon = FactoryDaemon(config, registry=registry,
        provider_factory=lambda *_: pytest.fail("must not generate twice"))
    daemon.journal.receive(job)
    completion = Completion(factory_id=job.factory_id, fencing_token=job.fencing_token,
                            result=ResultPackage(text="already generated"))
    daemon.journal.update(job.attempt_id, state="completion_pending",
                          completion=completion.model_dump(mode="json"))
    await daemon.execute(job, recovery=True)
    assert registry.currents == 0 and registry.completed == [completion]
    await close(daemon)


async def test_uncertain_synchronous_execution_requires_manual_retry(config, job):
    registry = Registry()
    daemon = FactoryDaemon(config, registry=registry,
        provider_factory=lambda *_: pytest.fail("must not repeat ambiguous submission"))
    daemon.journal.receive(job)
    daemon.journal.update(job.attempt_id, state="running")
    await daemon.execute(job, recovery=True)
    assert registry.failed[0].error.code == "execution_uncertain"
    assert not registry.failed[0].error.retryable
    await close(daemon)


async def test_async_operation_is_resumed_after_restart(config, job):
    class Resumer(EchoProvider):
        async def generate(self, job, directory, inputs, save_operation, operation):
            assert operation == {"name": "operations/already-submitted"}
            return text_output(job, "resumed")
    registry = Registry()
    daemon = FactoryDaemon(config, registry=registry, provider_factory=Resumer)
    daemon.journal.receive(job)
    daemon.journal.update(job.attempt_id, state="provider_pending",
                          operation={"name": "operations/already-submitted"})
    await daemon.execute(job, recovery=True)
    assert registry.completed[0].result.text == "resumed"
    await close(daemon)


async def test_uploaded_ref_survives_restart_without_reupload(config, job, tmp_path):
    registry = Registry()
    daemon = FactoryDaemon(config, registry=registry,
        provider_factory=lambda *_: pytest.fail("generated output must be reused"))
    daemon.journal.receive(job)
    path = tmp_path / "output.png"
    path.write_bytes(b"image")
    output = artifact_output(path, "image/png")
    output["files"][0]["ref"] = ArtifactRef(artifact_id="stored", sha256=hashlib.sha256(b"image").hexdigest(),
        mime_type="image/png", size_bytes=5).model_dump(mode="json")
    daemon.journal.update(job.attempt_id, state="generated", output=output)
    await daemon.execute(job, recovery=True)
    assert registry.uploads == 0
    assert registry.completed[0].result.artifacts[0].artifact_id == "stored"
    assert path.read_bytes() == b"image"  # kept until explicit future local retention policy
    await close(daemon)


async def test_shared_resource_group_never_runs_concurrently(config, job):
    EchoProvider.active = EchoProvider.maximum = 0
    registry = Registry()
    daemon = FactoryDaemon(config, registry=registry, provider_factory=EchoProvider)
    second = job.model_copy(update={"attempt_id": "attempt-2"})
    daemon.journal.receive(job)
    daemon.journal.receive(second)
    await asyncio.gather(daemon.execute(job, recovery=False), daemon.execute(second, recovery=False))
    assert EchoProvider.maximum == 1
    assert len(registry.completed) == 2
    await close(daemon)


async def test_lease_loss_stops_generation_and_rejects_output(config, job):
    class LostRegistry(Registry):
        async def renew(self, job):
            response = httpx.Response(409, request=httpx.Request("POST", "http://registry/renew"))
            raise httpx.HTTPStatusError("stale", request=response.request, response=response)
    registry = LostRegistry()
    daemon = FactoryDaemon(config, registry=registry, provider_factory=EchoProvider)
    daemon.journal.receive(job)
    await daemon.execute(job, recovery=False)
    assert not registry.completed and not registry.failed
    assert daemon.journal.get(job.attempt_id)["state"] == "stale"
    await close(daemon)


def test_journal_durable_on_reopen_and_portable_paths(tmp_path, job):
    journal = Journal(tmp_path)
    job.attempt_id = "namespace:attempt"
    journal.receive(job)
    journal.update(job.attempt_id, state="provider_pending", operation={"prompt_id": "durable"})
    assert ":" not in journal.attempt_dir(job.attempt_id).name
    journal.close()
    journal = Journal(tmp_path)
    assert json.loads(journal.pending()[0]["operation"]) == {"prompt_id": "durable"}
    journal.close()


def test_single_instance_lock(tmp_path):
    with InstanceLock(tmp_path), pytest.raises(OSError), InstanceLock(tmp_path):
        pass


async def test_input_checksum_verified_and_partial_removed(config, job, monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_FACTORY_TOKEN", "fixture")
    client = httpx.AsyncClient(base_url="http://registry.test/", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"wrong")))
    registry = RegistryClient(config, client)
    artifact = ArtifactRef(artifact_id="input", sha256=hashlib.sha256(b"right").hexdigest(),
                           size_bytes=5, mime_type="application/pdf")
    with pytest.raises(ValueError, match="checksum"):
        await registry.download(job, artifact, tmp_path / "input")
    assert not (tmp_path / "input").exists() and not (tmp_path / "input.partial").exists()
    await registry.close()


async def test_transient_completion_failure_retries_exact_output(config, job):
    class RetryRegistry(Registry):
        tries = 0
        async def complete(self, job, completion):
            self.tries += 1
            if self.tries == 1:
                raise httpx.ConnectError("connection dropped")
            await super().complete(job, completion)
    registry = RetryRegistry()
    daemon = FactoryDaemon(config, registry=registry, provider_factory=EchoProvider)
    daemon.journal.receive(job)
    await daemon.execute(job, recovery=False)
    assert registry.tries == 2 and len(registry.completed) == 1
    await close(daemon)


async def test_permanent_completion_rejection_fails_instead_of_looping(config, job):
    class RejectRegistry(Registry):
        async def complete(self, job, completion):
            response = httpx.Response(422, request=httpx.Request("POST", "http://registry/complete"))
            raise httpx.HTTPStatusError("validation error", request=response.request, response=response)
    registry = RejectRegistry()
    daemon = FactoryDaemon(config, registry=registry, provider_factory=EchoProvider)
    daemon.journal.receive(job)
    await asyncio.wait_for(daemon.execute(job, recovery=False), 2)
    assert registry.failed[0].error.code == "registry_rejected_output"
    assert not registry.failed[0].error.retryable
    assert daemon.journal.get(job.attempt_id)["completion"] is None
    await close(daemon)
