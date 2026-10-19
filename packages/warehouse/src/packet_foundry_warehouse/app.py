import asyncio
import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from packet_foundry_contracts import (
    ApplicationCreate,
    AttemptToken,
    ClaimRequest,
    Completion,
    Failure,
    Packet,
    PacketRecord,
    ResultEnvelope,
    SafeId,
    Specification,
    SpecificationRef,
    new_id,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .repository import WarehouseError, WarehouseRepository
from .settings import WarehouseSettings

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class PriorityUpdate(BaseModel):
    priority: int = Field(ge=0, le=100)


def create_app(settings: WarehouseSettings | None = None, *, initialize_schema: bool = False,
               export_transport=None) -> FastAPI:
    settings = settings or WarehouseSettings()
    repository = WarehouseRepository(settings)
    if initialize_schema:
        repository.initialize()
    if repository.engine.dialect.name != "postgresql" and not initialize_schema:
        raise ValueError("Warehouse service requires PostgreSQL; SQLite is only an explicit test helper")
    settings.artifact_path.mkdir(parents=True, exist_ok=True, mode=0o700)

    @asynccontextmanager
    async def lifespan(app):
        async def recover():
            while True:
                try:
                    await asyncio.to_thread(repository.reap_expired)
                except Exception:
                    logger.exception("Warehouse recovery pass failed")
                await asyncio.sleep(10)
        recovery = asyncio.create_task(recover())
        task = None
        if settings.langsmith_export:
            from .experiments import export_loop
            task = asyncio.create_task(export_loop(repository, transport=export_transport))
        try:
            yield
        finally:
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            repository.engine.dispose()

    app = FastAPI(title="Packet Foundry Warehouse", version="0.1.1", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.repository = repository
    app.state.settings = settings

    @app.exception_handler(WarehouseError)
    async def domain_error(_, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, exc):
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def conflicting_record(_, exc):
        return JSONResponse({"detail": "A conflicting record already exists"}, status_code=409)

    @app.middleware("http")
    async def manifest_limit(request: Request, call_next):
        if request.url.path not in {"/v1/artifacts", "/v1/internal/artifacts"}:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_manifest_bytes:
                    return JSONResponse({"detail": "manifest exceeds configured size"}, 413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        return await call_next(request)

    def credential(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if auth is None or auth.scheme.lower() != "bearer":
            raise HTTPException(401, "Bearer credential required", headers={"WWW-Authenticate": "Bearer"})
        return auth.credentials

    def admin(token: Annotated[str, Depends(credential)]):
        if not hmac.compare_digest(token, settings.admin_token.get_secret_value()):
            raise HTTPException(403, "admin credential required")

    def internal(token: Annotated[str, Depends(credential)]):
        if not hmac.compare_digest(token, settings.service_token.get_secret_value()):
            raise HTTPException(403, "registry service credential required")

    def application(token: Annotated[str, Depends(credential)]) -> str:
        identity = repository.authenticate(token)
        if identity is None:
            raise HTTPException(403, "application credential required")
        return identity

    @app.get("/health")
    def health():
        try:
            with repository.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok", "service": "warehouse"}

    @app.post("/v1/applications", dependencies=[Depends(admin)], status_code=201)
    def create_application(body: ApplicationCreate):
        return repository.create_application(body)

    @app.patch("/v1/applications/{application_id}", dependencies=[Depends(admin)])
    def set_priority(application_id: SafeId, body: PriorityUpdate):
        repository.set_priority(application_id, body.priority)
        return {"application_id": application_id, "priority": body.priority}

    @app.post("/v1/applications/{application_id}/rotate-token", dependencies=[Depends(admin)])
    def rotate_application_token(application_id: SafeId):
        return {"application_id": application_id, "token": repository.rotate_token(application_id)}

    @app.post("/v1/applications/{application_id}/revoke", dependencies=[Depends(admin)])
    def revoke_application(application_id: SafeId):
        repository.rotate_token(application_id, revoke=True)
        return {"revoked": True}

    @app.post("/v1/specifications", response_model=SpecificationRef)
    def put_spec(body: Specification, identity: Annotated[str, Depends(application)]):
        return repository.put_spec(identity, body)

    @app.post("/v1/packets", response_model=PacketRecord, status_code=202)
    def submit(body: Packet, response: Response, identity: Annotated[str, Depends(application)]):
        record, replay = repository.submit(identity, body)
        response.status_code = 200 if replay else 202
        return record

    @app.get("/v1/packets/{packet_id}", response_model=PacketRecord)
    def get_packet(packet_id: SafeId, identity: Annotated[str, Depends(application)]):
        return repository.get_packet(identity, packet_id)

    @app.post("/v1/packets/{packet_id}/cancel", response_model=PacketRecord)
    def cancel(packet_id: SafeId, identity: Annotated[str, Depends(application)]):
        return repository.cancel(identity, packet_id)

    @app.get("/v1/results", response_model=list[ResultEnvelope])
    def results(identity: Annotated[str, Depends(application)],
                limit: int = Query(default=100, ge=1, le=1000), batch_id: str | None = None):
        return repository.results(identity, limit, batch_id)

    @app.post("/v1/results/{packet_id}/ack")
    def acknowledge(packet_id: SafeId, identity: Annotated[str, Depends(application)]):
        repository.acknowledge(identity, packet_id)
        return {"acknowledged": True}

    @app.post("/v1/batches/{batch_id}/seal")
    def seal(batch_id: SafeId, identity: Annotated[str, Depends(application)]):
        return repository.batch(identity, batch_id, seal=True)

    @app.get("/v1/batches/{batch_id}")
    def batch(batch_id: SafeId, identity: Annotated[str, Depends(application)]):
        return repository.batch(identity, batch_id)

    @app.post("/v1/internal/claims", dependencies=[Depends(internal)])
    def claim(body: ClaimRequest):
        return repository.claim(body)

    @app.post("/v1/internal/attempts/{attempt_id}/renew", dependencies=[Depends(internal)])
    def renew(attempt_id: SafeId, body: AttemptToken):
        return {"lease_expires_at": repository.renew(attempt_id, body)}

    @app.get("/v1/internal/attempts/{attempt_id}", dependencies=[Depends(internal)])
    def assignment(attempt_id: SafeId, x_factory_id: Annotated[SafeId, Header()],
                   x_fencing_token: Annotated[int, Header(ge=1)]):
        return repository.assignment(attempt_id, AttemptToken(
            factory_id=x_factory_id, fencing_token=x_fencing_token))

    @app.post("/v1/internal/attempts/{attempt_id}/complete", dependencies=[Depends(internal)])
    def complete(attempt_id: SafeId, body: Completion):
        return repository.complete(attempt_id, body)

    @app.post("/v1/internal/attempts/{attempt_id}/fail", dependencies=[Depends(internal)])
    def fail(attempt_id: SafeId, body: Failure):
        return repository.fail(attempt_id, body)

    @app.get("/v1/internal/demand", dependencies=[Depends(internal)])
    def demand():
        return repository.demand()

    @app.get("/v1/internal/factories/{factory_id}/activity", dependencies=[Depends(internal)])
    def activity(factory_id: SafeId):
        return {"active_attempts": repository.activity(factory_id)}

    async def upload(request: Request, digest: str, app_id: str | None, *,
                     attempt_id=None, token=None):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise WarehouseError(422, "X-Content-SHA256 must be a lowercase SHA256 digest")
        mime_type = request.headers.get("content-type", "application/octet-stream").split(";")[0]
        if len(mime_type) > 150 or "/" not in mime_type:
            raise WarehouseError(422, "invalid Content-Type")
        if attempt_id:
            repository.assignment(attempt_id, token)
        artifact_id = new_id()
        root = settings.artifact_path.resolve()
        partial, target = root / f".{artifact_id}.partial", root / artifact_id
        size, hasher = 0, hashlib.sha256()
        committed = False
        try:
            with partial.open("xb") as output:
                if os.name != "nt":
                    partial.chmod(0o600)
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_artifact_bytes:
                        raise WarehouseError(413, "artifact exceeds configured size")
                    hasher.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if hasher.hexdigest() != digest:
                raise WarehouseError(422, "artifact checksum does not match")
            partial.replace(target)
            ref = repository.save_artifact(app_id, artifact_id, digest, size, mime_type,
                                          str(target), attempt_id=attempt_id, token=token)
            committed = True
            return ref
        finally:
            partial.unlink(missing_ok=True)
            if not committed:
                target.unlink(missing_ok=True)

    @app.post("/v1/artifacts")
    async def upload_input(request: Request, identity: Annotated[str, Depends(application)],
                           x_content_sha256: Annotated[str, Header()]):
        return await upload(request, x_content_sha256, identity)

    @app.get("/v1/artifacts/{artifact_id}")
    def download_output(artifact_id: SafeId, identity: Annotated[str, Depends(application)]):
        artifact = repository.artifact(identity, artifact_id)
        if not Path(artifact.path).is_file():
            raise WarehouseError(503, "artifact storage is unavailable")
        return FileResponse(artifact.path, media_type=artifact.mime_type,
                            headers={"X-Content-SHA256": artifact.sha256})

    @app.post("/v1/internal/artifacts", dependencies=[Depends(internal)])
    async def upload_output(request: Request, attempt_id: SafeId,
                            x_factory_id: Annotated[SafeId, Header()],
                            x_fencing_token: Annotated[int, Header(ge=1)],
                            x_content_sha256: Annotated[str, Header()]):
        token = AttemptToken(factory_id=x_factory_id, fencing_token=x_fencing_token)
        return await upload(request, x_content_sha256, None, attempt_id=attempt_id, token=token)

    @app.get("/v1/internal/artifacts/{artifact_id}", dependencies=[Depends(internal)])
    def download_input(artifact_id: SafeId, attempt_id: SafeId,
                       x_factory_id: Annotated[SafeId, Header()],
                       x_fencing_token: Annotated[int, Header(ge=1)]):
        token = AttemptToken(factory_id=x_factory_id, fencing_token=x_fencing_token)
        artifact = repository.input_artifact(attempt_id, token, artifact_id)
        if not Path(artifact.path).is_file():
            raise WarehouseError(503, "artifact storage is unavailable")
        return FileResponse(artifact.path, media_type=artifact.mime_type,
                            headers={"X-Content-SHA256": artifact.sha256})

    return app
