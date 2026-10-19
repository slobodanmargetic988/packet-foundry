import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from packet_foundry_contracts import (
    ApplicationCreate,
    AttemptToken,
    ClaimRequest,
    Completion,
    ExecutionProfile,
    Failure,
    GenerationRequest,
    Material,
    OutputSpec,
    Packet,
    ResultPackage,
    Specification,
    utcnow,
)
from packet_foundry_warehouse.app import create_app
from packet_foundry_warehouse.cli import migrate
from packet_foundry_warehouse.db import AttemptRow
from packet_foundry_warehouse.repository import WarehouseError, WarehouseRepository
from pydantic import ValidationError


def packet(key, *, app="alpha", **kwargs):
    request = kwargs.pop("request", GenerationRequest(instructions="Summarize this."))
    return Packet(application_id=app, idempotency_key=key,
                  request=request, **kwargs)


def claim(factory="factory-a", *, kinds=None, accept=True, groups=None):
    return ClaimRequest(factory_id=factory, profiles=[ExecutionProfile(profile_id="text",
        model="test-model", backend="fake", output_kinds=kinds or ["text", "json"],
        accept_experiments=accept)], resource_groups=groups or {"default": 1})


def token(assignment):
    return AttemptToken(factory_id=assignment.factory_id, fencing_token=assignment.fencing_token)


def finish(repo, assignment, text="done"):
    return repo.complete(assignment.attempt_id,
        Completion(**token(assignment).model_dump(), result=ResultPackage(text=text)))


def test_idempotency_is_scoped_and_content_checked(repository):
    for app in ("alpha", "beta"):
        repository.create_application(ApplicationCreate(application_id=app))
    original = packet("same")
    first, replay = repository.submit("alpha", original)
    assert not replay
    second, replay = repository.submit("alpha", packet("same"))
    assert replay and second.packet.packet_id == first.packet.packet_id
    with pytest.raises(WarehouseError, match="different content"):
        repository.submit("alpha", packet("same", priority=80))
    assert not repository.submit("beta", packet("same", app="beta"))[1]
    with pytest.raises(WarehouseError) as exc:
        repository.get_packet("beta", first.packet.packet_id)
    assert exc.value.status == 404


def test_shared_specs_are_pinned_and_schema_validated(repository):
    repository.create_application(ApplicationCreate(application_id="alpha"))
    spec = Specification(spec_id="style", version="v1", system_prompt="Be precise.",
                         instructions="No hashtags.", json_schema={"type": "object", "required": ["title"]})
    ref = repository.put_spec("alpha", spec)
    assert repository.put_spec("alpha", spec) == ref
    with pytest.raises(WarehouseError, match="immutable"):
        repository.put_spec("alpha", spec.model_copy(update={"instructions": "Different"}))
    p = packet("p", specifications=[ref], output=OutputSpec(kind="json", schema_ref=ref))
    repository.submit("alpha", p)
    job = repository.claim(claim())
    assert job.resolved_system_prompt == "Be precise."
    assert job.resolved_instructions == "No hashtags.\n\nSummarize this."
    with pytest.raises(WarehouseError, match="JSON Schema"):
        repository.complete(job.attempt_id, Completion(**token(job).model_dump(),
            result=ResultPackage(json_data={})))
    accepted = repository.complete(job.attempt_id, Completion(**token(job).model_dump(),
        result=ResultPackage(json_data={"title": "Valid"})))
    assert accepted.result.metadata["foundry_validation"]["checks"] == ["json_schema"]
    with pytest.raises(WarehouseError, match="local JSON"):
        repository.put_spec("alpha", Specification(spec_id="bad", version="v1",
                                                  json_schema={"$ref": "https://example.com/schema"}))
    with pytest.raises(WarehouseError, match="unresolved local"):
        repository.put_spec("alpha", Specification(spec_id="missing", version="v1",
                                                  json_schema={"$ref": "#/$defs/missing"}))


def test_live_app_priority_deadline_fifo_and_experiments(repository):
    repository.create_application(ApplicationCreate(application_id="alpha", priority=10))
    repository.create_application(ApplicationCreate(application_id="beta", priority=90))
    packets = [packet("experimental", priority=0), packet("normal", priority=50),
               packet("important", app="beta", priority=20), packet("live", priority=1, live=True),
               packet("later", priority=100, not_before=utcnow() + timedelta(days=1))]
    for p in packets:
        repository.submit(p.application_id, p)
    keys = []
    for _ in range(4):
        job = repository.claim(claim())
        keys.append(job.packet.idempotency_key)
        finish(repository, job)
    assert keys == ["live", "important", "normal", "experimental"]
    assert repository.claim(claim()) is None
    with pytest.raises(ValidationError):
        packet("invalid", priority=0, live=True)


def test_exact_models_and_cross_profile_priority(repository):
    repository.create_application(ApplicationCreate(application_id="alpha"))
    repository.submit("alpha", packet("pinned", request=GenerationRequest(
        instructions="Specific", model="unavailable")))
    assert repository.claim(claim()) is None
    repository.submit("alpha", packet("image-live", live=True, output=OutputSpec(kind="image")))
    repository.submit("alpha", packet("text", priority=100))
    request = claim()
    request.profiles.append(ExecutionProfile(profile_id="image", model="image-model", backend="fake",
                                             output_kinds=["image"]))
    job = repository.claim(request)
    assert job.packet.idempotency_key == "image-live"
    assert job.profile.profile_id == "image"


def test_atomic_claims_enforce_shared_capacity(repository):
    repository.create_application(ApplicationCreate(application_id="alpha"))
    for i in range(16):
        repository.submit("alpha", packet(str(i)))
    with ThreadPoolExecutor(max_workers=8) as pool:
        assignments = list(pool.map(lambda _: repository.claim(claim()), range(8)))
    assert len([a for a in assignments if a]) == 1
    other = repository.claim(claim("factory-b"))
    assert other and other.packet.packet_id != next(a for a in assignments if a).packet.packet_id


def test_expiry_fences_late_results_and_completion_replays(repository):
    repository.create_application(ApplicationCreate(application_id="alpha"))
    p = packet("p")
    repository.submit("alpha", p)
    old = repository.claim(claim())
    with repository.sessions.begin() as s:
        row = s.get(AttemptRow, old.attempt_id)
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
    current = repository.claim(claim("factory-b"))
    assert current.packet.packet_id == p.packet_id
    assert current.fencing_token > old.fencing_token
    with pytest.raises(WarehouseError):
        finish(repository, old, "late")
    assert finish(repository, current).status == "succeeded"
    assert finish(repository, current).status == "succeeded"
    with pytest.raises(WarehouseError, match="differs"):
        finish(repository, current, "different")
    with pytest.raises(WarehouseError):
        repository.renew(old.attempt_id, token(old))


def test_cancellation_and_ack_do_not_delete(repository):
    repository.create_application(ApplicationCreate(application_id="alpha"))
    p = packet("p")
    repository.submit("alpha", p)
    assignment = repository.claim(claim())
    repository.cancel("alpha", p.packet_id)
    with pytest.raises(WarehouseError):
        finish(repository, assignment)
    assert repository.results("alpha")[0].status == "cancelled"
    repository.acknowledge("alpha", p.packet_id)
    assert repository.results("alpha") == []
    assert repository.get_packet("alpha", p.packet_id).status == "cancelled"


def test_cancellation_racing_renewal_has_no_database_deadlock(repository):
    repository.create_application(ApplicationCreate(application_id="alpha"))
    for index in range(12):
        p = packet("race-" + str(index))
        repository.submit("alpha", p)
        assignment = repository.claim(claim())
        barrier = Barrier(2)

        def renew(barrier=barrier, assignment=assignment):
            barrier.wait(timeout=5)
            try:
                repository.renew(assignment.attempt_id, token(assignment))
            except WarehouseError as exc:
                assert exc.status == 409

        def cancel(barrier=barrier, p=p):
            barrier.wait(timeout=5)
            return repository.cancel("alpha", p.packet_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(renew), pool.submit(cancel)]
            for future in futures:
                future.result(timeout=10)
        assert repository.get_packet("alpha", p.packet_id).status == "cancelled"


def test_failure_retries_are_bounded_and_results_survive_ack(repository):
    repository.create_application(ApplicationCreate(application_id="alpha"))
    p = packet("bounded")
    repository.submit("alpha", p)
    for _ in range(repository.settings.max_attempts):
        assignment = repository.claim(claim())
        with repository.sessions.begin() as s:
            row = s.get(AttemptRow, assignment.attempt_id)
            row.lease_expires_at = utcnow() - timedelta(seconds=1)
        repository.reap_expired()
    result = repository.results("alpha")[0]
    assert result.status == "failed" and result.error.code == "lease_expired"
    assert repository.claim(claim()) is None
    repository.acknowledge("alpha", p.packet_id)
    assert repository.get_packet("alpha", p.packet_id).attempts == repository.settings.max_attempts


def test_retryable_failure_obeys_backoff(repository):
    from packet_foundry_contracts import ErrorInfo
    repository.create_application(ApplicationCreate(application_id="alpha"))
    repository.submit("alpha", packet("backoff"))
    assignment = repository.claim(claim())
    result = repository.fail(assignment.attempt_id, Failure(**token(assignment).model_dump(),
        error=ErrorInfo(code="throttled", message="Try later", retryable=True)))
    assert result.status == "queued"
    assert repository.claim(claim()) is None
    assert repository.results("alpha") == []


def test_application_credentials_rotate_and_revoke(repository):
    issued = repository.create_application(ApplicationCreate(application_id="alpha"))
    assert repository.authenticate(issued["token"]) == "alpha"
    new = repository.rotate_token("alpha")
    assert repository.authenticate(issued["token"]) is None
    assert repository.authenticate(new) == "alpha"
    repository.rotate_token("alpha", revoke=True)
    assert repository.authenticate(new) is None


def test_api_scopes_checksum_and_artifact_visibility(warehouse_settings):
    app = create_app(warehouse_settings, initialize_schema=True)
    with TestClient(app) as client:
        assert client.post("/v1/packets", json={}).status_code == 401
        admin = {"Authorization": "Bearer " + "a" * 32}
        issued = client.post("/v1/applications", json={"application_id": "alpha"}, headers=admin).json()
        headers = {"Authorization": "Bearer " + issued["token"]}
        data = b"test input bytes"
        upload = client.post("/v1/artifacts", content=data, headers={**headers,
            "X-Content-SHA256": hashlib.sha256(data).hexdigest(), "Content-Type": "image/png"})
        assert upload.status_code == 200, upload.text
        ref = upload.json()
        assert client.get("/v1/artifacts/" + ref["artifact_id"], headers=headers).content == data
        bad = client.post("/v1/artifacts", content=data,
            headers={**headers, "X-Content-SHA256": "0" * 64})
        assert bad.status_code == 422
        assert not list(warehouse_settings.artifact_path.glob("*.partial"))
        p = packet("p", materials=[Material(kind="image", artifact=ref)])
        assert client.post("/v1/packets", json=p.model_dump(mode="json"), headers=headers).status_code == 202
        assert client.post("/v1/internal/claims", json=claim().model_dump(), headers=headers).status_code == 403
        other = client.post("/v1/applications", json={"application_id": "beta"}, headers=admin).json()
        assert client.get("/v1/artifacts/" + ref["artifact_id"],
            headers={"Authorization": "Bearer " + other["token"]}).status_code == 404


def test_migrations_and_schema_match(warehouse_settings):
    migrate(warehouse_settings)
    repo = WarehouseRepository(warehouse_settings)
    assert repo.create_application(ApplicationCreate(application_id="alpha"))["token"]
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from packet_foundry_warehouse.db import Base
    with repo.engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    repo.engine.dispose()
