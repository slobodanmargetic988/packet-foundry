import json
from datetime import timedelta

import httpx
import pytest
from packet_foundry_contracts import (
    ExecutionProfile,
    FactoryRegistration,
    GenerationRequest,
    OutputSpec,
    Packet,
    utcnow,
)
from packet_foundry_registry.database import Base, CloudPod, Factory, make_engine, sessions
from packet_foundry_registry.runpod import RunpodController, RunpodError
from packet_foundry_registry.settings import RegistrySettings, RunpodTemplate, load_settings
from sqlalchemy import select


class Provider:
    def __init__(self):
        self.pods = []
        self.calls = []
        self.unknown_create = False
        self.creates = 0

    async def handle(self, request):
        path = request.url.path.removeprefix("/v1/")
        body = json.loads(await request.aread()) if request.content else None
        self.calls.append((request.method, path, body))
        if path == "pods" and request.method == "GET":
            return httpx.Response(200, json=self.pods)
        if path == "pods" and request.method == "POST":
            self.creates += 1
            provider_id = "pod-one" if self.creates == 1 else "pod-" + str(self.creates)
            pod = {"id": provider_id, "name": body["name"], "desiredStatus": "RUNNING"}
            self.pods.append(pod)
            if self.unknown_create:
                raise httpx.ReadTimeout("opaque provider response", request=request)
            return httpx.Response(201, json=pod)
        if path.endswith("/stop"):
            self.pods[0]["desiredStatus"] = "EXITED"
            return httpx.Response(204)
        if path.endswith("/start"):
            self.pods[0]["desiredStatus"] = "RUNNING"
            return httpx.Response(204)
        if request.method == "DELETE":
            self.pods.clear()
            return httpx.Response(204)
        raise AssertionError(f"Unexpected provider route {request.method} {path}")


@pytest.fixture
async def cloud(monkeypatch):
    provider = Provider()
    packet = Packet(application_id="app", idempotency_key="case",
                    request=GenerationRequest(instructions="Write a sentence"))
    state = {"queued": [packet], "active": 0, "warehouse_failed": False}

    async def warehouse(request):
        if state["warehouse_failed"]:
            return httpx.Response(503)
        if request.url.path == "/v1/internal/demand":
            return httpx.Response(200, json=[p.model_dump(mode="json") for p in state["queued"]])
        return httpx.Response(200, json={"active_attempts": state["active"]})

    template = RunpodTemplate(template_id="text-gpu", pod_parameters={
        "imageName": "example/operator-factory:fixed-version", "gpuTypeIds": ["example-gpu"],
    }, profiles=[ExecutionProfile(profile_id="text", model="model", backend="ollama")],
        idle_timeout_seconds=1,
        factory_config={"backends": {"ollama": {"kind": "ollama", "auth_mode": "none"}}},
        environment_from={"PROVIDER_API_KEY": "TEST_RUNPOD_PROVIDER_KEY"})
    settings = RegistrySettings(
        database_url="sqlite:///:memory:", admin_token="a" * 32, warehouse_token="s" * 32,
        public_url="https://registry.example.test", runpod_enabled=True,
        runpod_api_key="test-runpod-key", runpod_templates=[template],
    )
    monkeypatch.setenv("TEST_RUNPOD_PROVIDER_KEY", "test-private-key")
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessions(engine)
    async with httpx.AsyncClient(base_url="http://warehouse.test", transport=httpx.MockTransport(warehouse)) as client:
        controller = RunpodController(settings, engine, session_factory, client,
                                      transport=httpx.MockTransport(provider.handle))
        await controller.open()
        yield controller, provider, state, template
        await controller.close()
    engine.dispose()


def first(controller):
    with controller.sessions() as session:
        return session.scalar(select(CloudPod))


def ready(controller, template):
    pod = first(controller)
    with controller.sessions.begin() as session:
        factory = session.get(Factory, pod.factory_id)
        factory.registration = FactoryRegistration(
            factory_id=factory.factory_id, name=factory.name, profiles=template.profiles,
            resource_groups=template.resource_groups,
        ).model_dump(mode="json")
        factory.last_seen_at = utcnow()


@pytest.mark.asyncio
async def test_create_has_unique_enrollment_and_nonsecret_persistence(cloud):
    controller, provider, _, _ = cloud
    await controller.tick()
    pod = first(controller)
    assert pod.state == "starting"
    create = next(body for method, path, body in provider.calls if method == "POST" and path == "pods")
    env = create["env"]
    assert env["FOUNDRY_FACTORY_ID"] == pod.factory_id
    assert env["PROVIDER_API_KEY"] == "test-private-key"
    config = json.loads(env["FOUNDRY_FACTORY_CONFIG_JSON"])
    assert config["registry_url"] == "https://registry.example.test"
    assert config["registration"]["factory_id"] == pod.factory_id
    with controller.sessions() as session:
        factory = session.get(Factory, pod.factory_id)
        assert env["FOUNDRY_FACTORY_TOKEN"] not in factory.token_digest
    assert "test-private-key" not in repr(pod.__dict__)
    await controller.tick()
    assert len([1 for method, path, _ in provider.calls if method == "POST" and path == "pods"]) == 1


@pytest.mark.asyncio
async def test_unknown_create_reconciles_inventory_without_duplicate(cloud):
    controller, provider, _, _ = cloud
    provider.unknown_create = True
    await controller.tick()
    assert first(controller).state == "create_unknown"
    await controller.tick()
    assert first(controller).provider_pod_id == "pod-one"
    assert first(controller).state == "starting"
    assert len([1 for method, path, _ in provider.calls if method == "POST" and path == "pods"]) == 1


@pytest.mark.asyncio
async def test_idle_stop_and_demand_resume_owned_pod(cloud):
    controller, provider, state, template = cloud
    await controller.tick()
    ready(controller, template)
    await controller.tick()
    assert first(controller).state == "running"
    with controller.sessions.begin() as session:
        session.get(CloudPod, first(controller).record_id).last_busy_at = utcnow() - timedelta(seconds=10)
    saved = state["queued"]
    state["queued"] = []
    await controller.tick()
    assert first(controller).state == "stopped"
    assert any(path == "pods/pod-one/stop" for _, path, _ in provider.calls)
    state["queued"] = saved
    await controller.tick()
    assert first(controller).state == "starting"
    assert any(path == "pods/pod-one/start" for _, path, _ in provider.calls)
    assert len([1 for method, path, _ in provider.calls if method == "POST" and path == "pods"]) == 1


@pytest.mark.asyncio
async def test_experiments_and_incompatible_demand_never_start_pods(cloud):
    controller, provider, state, _ = cloud
    state["queued"][0].priority = 0
    await controller.tick()
    assert not provider.pods
    state["queued"][0].priority = 50
    state["queued"][0].output = OutputSpec(kind="video")
    await controller.tick()
    assert not provider.pods


@pytest.mark.asyncio
async def test_active_attempts_prevent_shutdown(cloud):
    controller, provider, state, template = cloud
    await controller.tick()
    ready(controller, template)
    await controller.tick()
    with controller.sessions.begin() as session:
        session.get(CloudPod, first(controller).record_id).last_busy_at = utcnow() - timedelta(seconds=10)
    state["queued"] = []
    state["active"] = 1
    await controller.tick()
    assert first(controller).state == "running"
    assert not any(path.endswith("/stop") for _, path, _ in provider.calls)


@pytest.mark.asyncio
async def test_offline_running_factory_cannot_keep_capacity_forever_under_pending_demand(cloud):
    controller, provider, state, template = cloud
    template.shutdown_action = "terminate"
    await controller.tick()
    ready(controller, template)
    await controller.tick()
    pod = first(controller)
    with controller.sessions.begin() as session:
        session.get(Factory, pod.factory_id).last_seen_at = utcnow() - timedelta(seconds=120)
    # Missing heartbeats alone cannot abort a still-valid warehouse attempt.
    state["active"] = 1
    await controller.tick()
    assert first(controller).state == "running"
    assert not any(path.endswith("/stop") or method == "DELETE"
                   for method, path, _ in provider.calls)
    state["active"] = 0
    await controller.tick()
    assert first(controller).state == "offline_failed"
    assert first(controller).error_code == "factory_offline"
    assert any(path.endswith("/stop") for _, path, _ in provider.calls)
    assert not any(method == "DELETE" for method, _, _ in provider.calls)
    # Queued demand must not restart or replace the same broken runtime forever.
    await controller.tick()
    assert first(controller).state == "offline_failed"
    assert provider.creates == 1
    assert not any(path.endswith("/start") for _, path, _ in provider.calls)
    assert await controller.recover(pod.record_id) == {"state": "starting"}


@pytest.mark.asyncio
async def test_never_touch_unowned_pods_and_removed_template_drains_owned(cloud):
    controller, provider, state, template = cloud
    await controller.tick()
    ready(controller, template)
    await controller.tick()
    provider.pods.append({"id": "unrelated", "name": "operator-other-work", "desiredStatus": "RUNNING"})
    controller.settings.runpod_templates = []
    state["queued"] = []
    await controller.tick()
    assert first(controller).state == "stopped"
    assert not any("unrelated" in path for _, path, _ in provider.calls)


@pytest.mark.asyncio
async def test_warehouse_outage_prevents_shutdown(cloud):
    controller, provider, state, template = cloud
    await controller.tick()
    ready(controller, template)
    await controller.tick()
    state["warehouse_failed"] = True
    with pytest.raises(Exception, match="warehouse_demand_unavailable"):
        await controller.tick()
    assert not any(path.endswith("/stop") for _, path, _ in provider.calls)


@pytest.mark.asyncio
async def test_startup_timeout_stops_and_quarantines_instead_of_restart_loop(cloud):
    controller, provider, _, template = cloud
    await controller.tick()
    with controller.sessions.begin() as session:
        row = session.get(CloudPod, first(controller).record_id)
        row.updated_at = utcnow() - timedelta(seconds=template.startup_timeout_seconds + 1)
    await controller.tick()
    assert first(controller).state == "startup_failed"
    await controller.tick()
    assert first(controller).state == "startup_failed"
    assert not any(path.endswith("/start") for _, path, _ in provider.calls)
    assert await controller.recover(first(controller).record_id) == {"state": "starting"}
    assert first(controller).state == "starting"


@pytest.mark.asyncio
async def test_restart_reconciles_persisted_unknown_create(cloud):
    controller, provider, _, _ = cloud
    provider.unknown_create = True
    await controller.tick()
    restarted = RunpodController(controller.settings, controller.engine, controller.sessions,
                                 controller.warehouse, transport=httpx.MockTransport(provider.handle))
    await restarted.open()
    try:
        await restarted.tick()
        assert first(restarted).provider_pod_id == "pod-one"
        assert len([1 for method, path, _ in provider.calls if method == "POST" and path == "pods"]) == 1
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_confirmed_absent_unknown_can_be_replaced_and_late_pod_is_stopped(cloud):
    controller, provider, _, _ = cloud
    provider.unknown_create = True
    await controller.tick()
    old = first(controller)
    delayed_pod = provider.pods.pop()
    with pytest.raises(RunpodError, match="review_provider_inventory"):
        await controller.recover(old.record_id)
    assert await controller.recover(old.record_id, confirm_absent=True) == {"state": "abandoned"}
    with controller.sessions() as session:
        assert session.get(Factory, old.factory_id).revoked
    provider.pods.append(delayed_pod)
    provider.unknown_create = False
    await controller.tick()
    with controller.sessions() as session:
        assert session.get(CloudPod, old.record_id).state == "abandoned"
    assert delayed_pod["desiredStatus"] == "EXITED"
    assert provider.creates == 2


def test_template_rejects_secret_storage_and_invalid_resources(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"runpod_api_key": "private-key"}))
    with pytest.raises(ValueError, match="credential"):
        load_settings(path)
    with pytest.raises(ValueError, match="name/env"):
        RunpodTemplate(template_id="x", profiles=[ExecutionProfile(profile_id="x", model="x", backend="x")],
                       pod_parameters={"imageName": "image", "env": {"PRIVATE": "secret"}})
