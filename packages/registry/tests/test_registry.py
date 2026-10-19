from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from packet_foundry_contracts import (
    Assignment,
    ExecutionProfile,
    FactoryRegistration,
    GenerationRequest,
    Packet,
    utcnow,
)
from packet_foundry_registry.app import create_app
from packet_foundry_registry.database import Factory
from packet_foundry_registry.settings import RegistrySettings
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

ADMIN = "registry-test-admin-credential-32-characters"
SERVICE = "registry-test-service-credential-32-characters"


def registration(factory_id="windows-one"):
    return FactoryRegistration(factory_id=factory_id, name="Windows laptop", profiles=[
        ExecutionProfile(profile_id="text", model="local-model", backend="ollama"),
        ExecutionProfile(profile_id="image", model="image-model", backend="comfyui",
                         output_kinds=["image"]),
    ]).model_dump(mode="json")


class WarehouseTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []
        self.claims = []
        self.status = 200
        self.upload_chunks = []
        self.upload_was_streaming = False
        self.closed = False
        self.assignment = None

    async def handle_async_request(self, request):
        assert request.headers["authorization"] == "Bearer " + SERVICE
        self.requests.append(request)
        if request.url.path == "/v1/internal/claims":
            import json

            self.claims.append(json.loads(await request.aread()))
            if self.status != 200:
                return httpx.Response(self.status, json={"detail": "upstream private detail"})
            return (httpx.Response(200, json=self.assignment) if self.assignment is not None
                    else httpx.Response(200, content=b"null"))
        if request.url.path == "/v1/internal/artifacts" and request.method == "POST":
            try:
                _ = request.content
            except httpx.RequestNotRead:
                self.upload_was_streaming = True
            async for chunk in request.stream:
                self.upload_chunks.append(chunk)
            return httpx.Response(200, json={"artifact_id": "out"})
        if request.url.path.startswith("/v1/internal/artifacts/"):
            return httpx.Response(200, headers={"Content-Type": "image/png"},
                                  stream=Chunks([b"first", b"second"]))
        return httpx.Response(self.status, json={"ok": True})

    async def aclose(self):
        self.closed = True


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.fixture
def registry(tmp_path):
    transport = WarehouseTransport()
    settings = RegistrySettings(
        database_url="sqlite:///:memory:", admin_token=ADMIN, warehouse_token=SERVICE,
        warehouse_url="http://warehouse.test", poll_interval_seconds=0.001, max_manifest_bytes=1024,
    )
    app = create_app(settings, warehouse_transport=transport, initialize_schema=True)
    with TestClient(app) as client:
        yield client, app, transport
    assert transport.closed


def enrolled(client, factory_id="windows-one"):
    response = client.post("/v1/enrollments", json={"factory_id": factory_id, "name": "Laptop"},
                           headers={"Authorization": "Bearer " + ADMIN})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def registered(client):
    headers = enrolled(client)
    assert client.post("/v1/register", json=registration(), headers=headers).status_code == 200
    return headers


def test_authentication_and_hashed_scoped_credentials(registry):
    client, app, _ = registry
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 404
    assert client.get("/v1/factories").status_code == 401
    headers = enrolled(client)
    raw = headers["Authorization"].removeprefix("Bearer ")
    assert client.get("/v1/factories", headers=headers).status_code == 401
    with app.state.sessions() as session:
        row = session.get(Factory, "windows-one")
        assert raw not in row.token_digest
        assert len(row.token_digest) == 64
    listing = client.get("/v1/factories", headers={"Authorization": "Bearer " + ADMIN})
    assert raw not in listing.text
    assert "token_digest" not in listing.text
    duplicate = client.post("/v1/enrollments", json={"factory_id": "windows-one", "name": "New"},
                            headers={"Authorization": "Bearer " + ADMIN})
    assert duplicate.status_code == 409


def test_health_reports_unavailable_database_without_sensitive_details(registry, monkeypatch):
    client, app, _ = registry

    def unavailable():
        raise OperationalError("SELECT 1", {}, Exception("private-database-marker"))

    monkeypatch.setattr(app.state.engine, "connect", unavailable)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "private-database-marker" not in response.text


@pytest.mark.parametrize("admin_token,warehouse_token", [
    ("short", "s" * 32), ("a" * 32, "short"), ("same" * 8, "same" * 8),
])
def test_admin_and_service_credentials_require_distinct_strong_values(admin_token, warehouse_token):
    with pytest.raises(ValueError, match="distinct admin/service"):
        RegistrySettings(admin_token=admin_token, warehouse_token=warehouse_token)


def test_registration_and_attempt_identity_cannot_be_spoofed(registry):
    client, _, transport = registry
    headers = enrolled(client)
    assert client.post("/v1/register", json=registration("other"), headers=headers).status_code == 403
    assert client.post("/v1/work?wait_seconds=0", headers=headers).status_code == 409
    assert client.post("/v1/heartbeat", headers=headers).status_code == 409
    response = client.post("/v1/attempts/attempt/renew", headers=headers,
                           json={"factory_id": "other", "fencing_token": 1})
    assert response.status_code == 403
    assert not transport.requests


def test_claim_forwards_all_profiles_and_current_capacity(registry):
    client, _, transport = registry
    headers = registered(client)
    assert client.post("/v1/work?wait_seconds=0", headers=headers).json() is None
    assert [p["profile_id"] for p in transport.claims[0]["profiles"]] == ["text", "image"]
    assert transport.claims[0]["factory_id"] == "windows-one"
    assert transport.claims[0]["resource_groups"] == {"default": 1}
    assert client.post("/v1/heartbeat", headers=headers).json() == {"online": True}


def test_offline_revoked_and_draining_factories_cannot_claim(registry):
    client, app, transport = registry
    headers = registered(client)
    with app.state.sessions.begin() as session:
        session.get(Factory, "windows-one").last_seen_at = utcnow() - timedelta(seconds=120)
    assert client.post("/v1/work?wait_seconds=0", headers=headers).status_code == 409
    assert not transport.claims
    assert client.post("/v1/heartbeat", headers=headers).status_code == 200
    with app.state.sessions.begin() as session:
        session.get(Factory, "windows-one").draining = True
    assert client.post("/v1/work?wait_seconds=0", headers=headers).json() is None
    assert not transport.claims
    response = client.post("/v1/factories/windows-one/revoke",
                           headers={"Authorization": "Bearer " + ADMIN})
    assert response.status_code == 200
    assert client.post("/v1/heartbeat", headers=headers).status_code == 401


def test_attempt_forwarding_injects_authenticated_factory(registry):
    client, _, transport = registry
    headers = registered(client)
    headers["X-Fencing-Token"] = "7"
    headers["X-Factory-ID"] = "forged"
    assert client.get("/v1/attempts/attempt-one", headers=headers).status_code == 200
    request = transport.requests[-1]
    assert request.headers["x-factory-id"] == "windows-one"
    assert request.headers["x-fencing-token"] == "7"
    assert client.post("/v1/attempts/attempt-one/renew", headers=headers, json={
        "factory_id": "windows-one", "fencing_token": 7,
    }).status_code == 200


def test_upload_download_are_streamed_and_attempt_scoped(registry):
    client, _, transport = registry
    headers = registered(client)
    headers.update({"X-Fencing-Token": "4", "X-Content-SHA256": "a" * 64,
                    "Content-Type": "image/png"})
    response = client.post("/v1/artifacts?attempt_id=attempt-one", headers=headers,
                           content=iter([b"one", b"two", b"three"]))
    assert response.status_code == 200
    assert transport.upload_was_streaming
    assert b"".join(transport.upload_chunks) == b"onetwothree"
    request = transport.requests[-1]
    assert request.url.params["attempt_id"] == "attempt-one"
    assert request.headers["x-factory-id"] == "windows-one"
    response = client.get("/v1/artifacts/material?attempt_id=attempt-one", headers=headers)
    assert response.content == b"firstsecond"
    assert response.headers["content-type"] == "image/png"


def test_upstream_errors_do_not_leak_private_details(registry):
    client, _, transport = registry
    headers = registered(client)
    transport.status = 500
    response = client.post("/v1/work?wait_seconds=0", headers=headers)
    assert response.status_code == 503
    assert "private detail" not in response.text


def test_validation_errors_never_echo_supplied_credentials(registry):
    client, _, _ = registry
    headers = enrolled(client)
    response = client.post("/v1/register", json={"secret": "sensitive-marker"}, headers=headers)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert "sensitive-marker" not in response.text


def test_manifest_limit_bounds_anonymous_body_without_buffering_binary_upload(registry):
    client, _, transport = registry
    response = client.post("/v1/register", content=b"x" * 2048,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert not transport.requests
    headers = registered(client)
    headers.update({"X-Fencing-Token": "1", "X-Content-SHA256": "a" * 64})
    uploaded = client.post("/v1/artifacts?attempt_id=attempt-one", content=b"x" * 2048,
                           headers=headers)
    assert uploaded.status_code == 200
    assert transport.upload_was_streaming
    assert sum(len(chunk) for chunk in transport.upload_chunks) == 2048


def test_assignment_identity_is_checked(registry):
    client, app, transport = registry
    headers = registered(client)
    packet = Packet(application_id="example", idempotency_key="x",
                    request=GenerationRequest(instructions="Write one sentence"))
    assignment = Assignment(packet=packet, attempt_id="attempt", factory_id="another-factory",
                            fencing_token=1, lease_expires_at=utcnow() + timedelta(minutes=2),
                            profile=ExecutionProfile.model_validate(registration()["profiles"][0]),
                            resolved_instructions="Write one sentence")

    transport.assignment = assignment.model_dump(mode="json")
    response = client.post("/v1/work?wait_seconds=0", headers=headers)
    assert response.status_code == 502


def test_reenrollment_does_not_return_old_credential(registry):
    client, app, _ = registry
    enrolled(client)
    with app.state.sessions() as session:
        assert len(session.scalars(select(Factory)).all()) == 1


def test_rotated_credential_invalidates_old_and_requires_fresh_registration(registry):
    client, _, _ = registry
    headers = registered(client)
    rotated = client.post("/v1/factories/windows-one/rotate",
                          headers={"Authorization": "Bearer " + ADMIN})
    assert rotated.status_code == 200
    assert rotated.headers["cache-control"] == "no-store"
    assert client.post("/v1/heartbeat", headers=headers).status_code == 401
    replacement = {"Authorization": "Bearer " + rotated.json()["token"]}
    assert client.post("/v1/work?wait_seconds=0", headers=replacement).status_code == 409
    assert client.post("/v1/heartbeat", headers=replacement).status_code == 409
    assert client.post("/v1/register", json=registration(), headers=replacement).status_code == 200
