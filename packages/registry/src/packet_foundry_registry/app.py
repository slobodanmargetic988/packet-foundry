"""Private outbound-worker registry and streaming warehouse relay."""

import asyncio
import hmac
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from packet_foundry_contracts import (
    Assignment,
    AttemptToken,
    ClaimRequest,
    Completion,
    EnrollmentCreate,
    FactoryRegistration,
    Failure,
    SafeId,
    utcnow,
)
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .database import Base, CloudPod, Factory, advisory_lock, make_engine, sessions, utc
from .security import enroll, new_factory_token, token_digest
from .settings import RegistrySettings

bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def create_app(
    settings: RegistrySettings | None = None,
    *,
    warehouse_transport: httpx.AsyncBaseTransport | None = None,
    runpod_transport: httpx.AsyncBaseTransport | None = None,
    initialize_schema: bool = False,
) -> FastAPI:
    settings = settings or RegistrySettings()
    engine = make_engine(settings.database_url)
    if engine.dialect.name != "postgresql" and not initialize_schema:
        engine.dispose()
        raise ValueError("Production registry requires PostgreSQL")
    session_factory = sessions(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        controller_task = None
        controller = None
        try:
            if (not settings.admin_token.get_secret_value()
                    or not settings.warehouse_token.get_secret_value()):
                raise RuntimeError("Registry admin and warehouse credentials must be configured")
            if initialize_schema:
                Base.metadata.create_all(engine)
            async with httpx.AsyncClient(
                base_url=settings.warehouse_url.rstrip("/") + "/",
                headers={"Authorization": "Bearer " + settings.warehouse_token.get_secret_value(),
                         "Accept-Encoding": "identity"},
                timeout=httpx.Timeout(settings.warehouse_timeout_seconds, connect=5),
                transport=warehouse_transport,
                follow_redirects=False,
            ) as warehouse:
                app.state.warehouse = warehouse
                try:
                    if settings.runpod_enabled:
                        from .runpod import RunpodController

                        controller = RunpodController(
                            settings, engine, session_factory, warehouse, transport=runpod_transport,
                        )
                        await controller.open()
                        app.state.runpod = controller
                        controller_task = asyncio.create_task(
                            controller.run(), name="runpod-reconciler",
                        )
                    yield
                finally:
                    if controller_task:
                        controller_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await controller_task
                    if controller:
                        await controller.close()
        finally:
            engine.dispose()

    app = FastAPI(title="Packet Foundry Registry", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = session_factory

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(_request: Request, _error: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "Registry database unavailable"})

    def admin(credentials: Credentials) -> None:
        if not credentials or not hmac.compare_digest(
            credentials.credentials, settings.admin_token.get_secret_value(),
        ):
            raise HTTPException(401, "Invalid admin credential")

    def factory_identity(credentials: Credentials) -> str:
        if not credentials:
            raise HTTPException(401, "Factory credential required")
        with session_factory() as session:
            factory = session.scalar(select(Factory).where(
                Factory.token_digest == token_digest(credentials.credentials),
                Factory.revoked.is_(False),
            ))
            if factory is None:
                raise HTTPException(401, "Invalid factory credential")
            return factory.factory_id

    Admin = Annotated[None, Depends(admin)]
    Identity = Annotated[str, Depends(factory_identity)]

    def read_factory(factory_id: str, *, fresh: bool = False) -> Factory:
        with session_factory() as session:
            factory = session.get(Factory, factory_id)
            if factory is None or factory.revoked:
                raise HTTPException(401, "Invalid factory credential")
            if fresh and (
                not factory.registration or not factory.last_seen_at
                or utc(factory.last_seen_at) < utcnow() - timedelta(
                    seconds=settings.offline_after_seconds,
                )
            ):
                raise HTTPException(409, "Factory must register and heartbeat before requesting work")
            return factory

    async def relay(
        method: str, path: str, *, body: dict | None = None,
        headers: dict | None = None, params: dict | None = None,
    ):
        try:
            response = await app.state.warehouse.request(
                method, "v1/" + path, json=body, headers=headers, params=params,
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Warehouse unavailable") from None
        if not response.is_success:
            code = response.status_code if 400 <= response.status_code < 500 else 503
            raise HTTPException(code, "Warehouse rejected operation")
        try:
            return response.json()
        except ValueError:
            raise HTTPException(502, "Invalid warehouse response") from None

    def attempt_headers(factory_id: str, fencing_token: int) -> dict[str, str]:
        return {"X-Factory-ID": factory_id, "X-Fencing-Token": str(fencing_token)}

    def verify_identity(factory_id: str, token: AttemptToken) -> None:
        if token.factory_id != factory_id:
            raise HTTPException(403, "Attempt identity does not match factory credential")

    @app.get("/health")
    def health():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ok", "service": "registry"}

    @app.post("/v1/enrollments")
    async def enrollment(request: EnrollmentCreate, _admin: Admin):
        with session_factory() as session:
            try:
                token = enroll(session, request)
            except ValueError as error:
                raise HTTPException(409, str(error)) from None
        return JSONResponse({"factory_id": request.factory_id, "token": token},
                            headers={"Cache-Control": "no-store"})

    @app.get("/v1/factories")
    async def factories(_admin: Admin):
        with session_factory() as session:
            rows = session.scalars(select(Factory).order_by(Factory.factory_id)).all()
            return [{
                "factory_id": row.factory_id, "name": row.name, "revoked": row.revoked,
                "draining": row.draining, "registered": bool(row.registration),
                "online": bool(row.last_seen_at and utc(row.last_seen_at) >= utcnow() - timedelta(
                    seconds=settings.offline_after_seconds,
                ) and not row.revoked),
                "last_seen_at": utc(row.last_seen_at).isoformat() if row.last_seen_at else None,
                "registration": row.registration,
            } for row in rows]

    @app.post("/v1/factories/{factory_id}/revoke")
    async def revoke(factory_id: SafeId, _admin: Admin):
        with advisory_lock(engine, "factory:" + factory_id) as acquired:
            if not acquired:
                raise HTTPException(409, "Factory operation in progress; retry")
            with session_factory.begin() as session:
                row = session.get(Factory, factory_id)
                if row is None:
                    raise HTTPException(404, "Factory not found")
                row.revoked = True
                row.draining = True
        return {"revoked": True}

    @app.post("/v1/register")
    async def register(request: FactoryRegistration, factory_id: Identity):
        if request.factory_id != factory_id:
            raise HTTPException(403, "Registration identity does not match factory credential")
        with session_factory.begin() as session:
            row = session.get(Factory, factory_id)
            row.name = request.name
            row.registration = request.model_dump(mode="json")
            row.last_seen_at = utcnow()
        return {"registered": True}

    @app.post("/v1/factories/{factory_id}/rotate")
    async def rotate(factory_id: SafeId, _admin: Admin):
        with advisory_lock(engine, "factory:" + factory_id) as acquired:
            if not acquired:
                raise HTTPException(409, "Factory operation in progress; retry")
            token = new_factory_token()
            with session_factory.begin() as session:
                row = session.get(Factory, factory_id)
                if row is None:
                    raise HTTPException(404, "Factory not found")
                row.token_digest = token_digest(token)
                row.revoked = False
                row.last_seen_at = None
                row.registration = None
        return JSONResponse({"factory_id": factory_id, "token": token},
                            headers={"Cache-Control": "no-store"})

    @app.post("/v1/heartbeat")
    async def heartbeat(factory_id: Identity):
        with session_factory.begin() as session:
            row = session.get(Factory, factory_id)
            if not row.registration:
                raise HTTPException(409, "Factory must register first")
            row.last_seen_at = utcnow()
        return {"online": True}

    @app.post("/v1/work")
    async def work(
        request: Request, factory_id: Identity,
        wait_seconds: Annotated[float, Query(ge=0, le=25)] = 25,
    ):
        expires = asyncio.get_running_loop().time() + wait_seconds
        while True:
            with advisory_lock(engine, "factory:" + factory_id) as acquired:
                if acquired:
                    factory = read_factory(factory_id, fresh=True)
                    if factory.draining:
                        return None
                    registration = FactoryRegistration.model_validate(factory.registration)
                    claim = ClaimRequest(
                        factory_id=factory_id, profiles=registration.profiles,
                        resource_groups=registration.resource_groups,
                    )
                    value = await relay("POST", "internal/claims", body=claim.model_dump(mode="json"))
                    if value is not None:
                        try:
                            assignment = Assignment.model_validate(value)
                        except ValueError:
                            raise HTTPException(502, "Invalid warehouse assignment") from None
                        if assignment.factory_id != factory_id:
                            raise HTTPException(502, "Warehouse assignment identity mismatch")
                        with session_factory.begin() as session:
                            cloud = session.scalar(select(CloudPod).where(
                                CloudPod.factory_id == factory_id,
                            ))
                            if cloud:
                                cloud.last_busy_at = utcnow()
                        return assignment
            remaining = expires - asyncio.get_running_loop().time()
            if remaining <= 0 or await request.is_disconnected():
                return None
            await asyncio.sleep(min(settings.poll_interval_seconds, remaining))

    @app.get("/v1/attempts/{attempt_id}")
    async def attempt(
        attempt_id: SafeId, factory_id: Identity,
        fencing_token: Annotated[int, Header(alias="X-Fencing-Token", ge=1)],
    ):
        return await relay("GET", "internal/attempts/" + attempt_id,
                           headers=attempt_headers(factory_id, fencing_token))

    @app.post("/v1/attempts/{attempt_id}/renew")
    async def renew(attempt_id: SafeId, token: AttemptToken, factory_id: Identity):
        verify_identity(factory_id, token)
        return await relay("POST", f"internal/attempts/{attempt_id}/renew",
                           body=token.model_dump(mode="json"))

    @app.post("/v1/attempts/{attempt_id}/complete")
    async def complete(attempt_id: SafeId, completion: Completion, factory_id: Identity):
        verify_identity(factory_id, completion)
        return await relay("POST", f"internal/attempts/{attempt_id}/complete",
                           body=completion.model_dump(mode="json"))

    @app.post("/v1/attempts/{attempt_id}/fail")
    async def fail(attempt_id: SafeId, failure: Failure, factory_id: Identity):
        verify_identity(factory_id, failure)
        return await relay("POST", f"internal/attempts/{attempt_id}/fail",
                           body=failure.model_dump(mode="json"))

    @app.get("/v1/artifacts/{artifact_id}")
    async def download(
        artifact_id: SafeId, factory_id: Identity, attempt_id: SafeId,
        fencing_token: Annotated[int, Header(alias="X-Fencing-Token", ge=1)],
    ):
        try:
            upstream_request = app.state.warehouse.build_request(
                "GET", "v1/internal/artifacts/" + artifact_id, params={"attempt_id": attempt_id},
                headers=attempt_headers(factory_id, fencing_token),
            )
            upstream = await app.state.warehouse.send(upstream_request, stream=True)
        except httpx.HTTPError:
            raise HTTPException(503, "Warehouse unavailable") from None
        if not upstream.is_success:
            await upstream.aclose()
            code = upstream.status_code if 400 <= upstream.status_code < 500 else 503
            raise HTTPException(code, "Warehouse rejected artifact access")

        async def chunks():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        headers = {name: upstream.headers[name] for name in (
            "content-type", "content-length", "etag", "content-disposition", "x-content-sha256",
        ) if name in upstream.headers}
        return StreamingResponse(chunks(), status_code=upstream.status_code, headers=headers)

    @app.post("/v1/artifacts")
    async def upload(
        request: Request, factory_id: Identity, attempt_id: SafeId,
        fencing_token: Annotated[int, Header(alias="X-Fencing-Token", ge=1)],
        checksum: Annotated[str, Header(alias="X-Content-SHA256", pattern=r"^[a-f0-9]{64}$")],
    ):
        headers = attempt_headers(factory_id, fencing_token)
        headers.update({"X-Content-SHA256": checksum,
                        "Content-Type": request.headers.get("content-type", "application/octet-stream")})
        if "content-length" in request.headers:
            headers["Content-Length"] = request.headers["content-length"]
        try:
            response = await app.state.warehouse.post(
                "v1/internal/artifacts", params={"attempt_id": attempt_id},
                headers=headers, content=request.stream(),
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Warehouse unavailable") from None
        if not response.is_success:
            code = response.status_code if 400 <= response.status_code < 500 else 503
            raise HTTPException(code, "Warehouse rejected artifact upload")
        try:
            return response.json()
        except ValueError:
            raise HTTPException(502, "Invalid warehouse response") from None

    @app.get("/v1/runpod/pods")
    async def cloud_pods(_admin: Admin):
        with session_factory() as session:
            rows = session.scalars(select(CloudPod).order_by(CloudPod.created_at)).all()
            return [{
                "record_id": row.record_id, "template_id": row.template_id,
                "factory_id": row.factory_id, "pod_name": row.pod_name,
                "provider_pod_id": row.provider_pod_id, "state": row.state,
                "error_code": row.error_code,
            } for row in rows]

    @app.post("/v1/runpod/pods/{record_id}/recover")
    async def recover_pod(
        record_id: SafeId, _admin: Admin,
        confirm_absent: Annotated[bool, Header(alias="X-Confirm-Provider-Absence")] = False,
    ):
        if not settings.runpod_enabled:
            raise HTTPException(409, "Runpod is not enabled")
        from .runpod import RunpodError

        try:
            return await app.state.runpod.recover(record_id, confirm_absent=confirm_absent)
        except RunpodError as error:
            raise HTTPException(409, str(error)) from None
        except httpx.HTTPError:
            raise HTTPException(503, "Runpod unavailable") from None

    return app
