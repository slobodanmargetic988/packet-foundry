from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import jsonschema
from packet_foundry_contracts import (
    ApplicationCreate,
    ArtifactRef,
    Assignment,
    AttemptToken,
    ClaimRequest,
    Completion,
    ErrorInfo,
    Failure,
    Packet,
    PacketRecord,
    ResultEnvelope,
    Specification,
    SpecificationRef,
    digest_json,
    new_id,
    utcnow,
)
from referencing.exceptions import Unresolvable
from sqlalchemy import case, create_engine, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from .comparison import comparison_inputs
from .db import (
    ApplicationRow,
    ArtifactRow,
    AttemptRow,
    Base,
    BatchRow,
    ComparisonCaseRow,
    PacketRow,
    SpecificationRow,
)
from .settings import WarehouseSettings


class WarehouseError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def validate_schema(schema: dict | None) -> None:
    if schema is None:
        return
    refs, anchors = [], set()
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and not str(item).startswith("#"):
                    raise WarehouseError(422, "only local JSON Schema references are supported")
                if key in {"$ref", "$dynamicRef"}:
                    refs.append(str(item))
                if key in {"$anchor", "$dynamicAnchor"}:
                    anchors.add(str(item))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(schema)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise WarehouseError(422, "invalid output JSON Schema") from exc
    for ref in refs:
        if ref == "#":
            continue
        fragment = unquote(ref[1:])
        if not fragment.startswith("/"):
            if fragment not in anchors:
                raise WarehouseError(422, "unresolved local JSON Schema reference")
            continue
        current = schema
        try:
            for segment in fragment[1:].split("/"):
                segment = segment.replace("~1", "/").replace("~0", "~")
                current = current[int(segment)] if isinstance(current, list) else current[segment]
        except (KeyError, ValueError, IndexError, TypeError) as exc:
            raise WarehouseError(422, "unresolved local JSON Schema reference") from exc


class WarehouseRepository:
    def __init__(self, settings: WarehouseSettings):
        self.settings = settings
        options = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        self.engine = create_engine(settings.database_url, connect_args=options, pool_pre_ping=True,
                                    hide_parameters=True)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        """Explicit test/development helper. Production uses Alembic migrations."""
        Base.metadata.create_all(self.engine)

    def lock(self, session: Session, key: str):
        if self.engine.dialect.name == "postgresql":
            lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})

    def authenticate(self, token: str) -> str | None:
        with self.sessions() as s:
            return s.scalar(select(ApplicationRow.application_id).where(
                ApplicationRow.token_hash == token_hash(token)))

    def create_application(self, body: ApplicationCreate) -> dict:
        with self.sessions.begin() as s:
            self.lock(s, f"app:{body.application_id}")
            if s.get(ApplicationRow, body.application_id):
                raise WarehouseError(409, "application already exists; rotate explicitly")
            token = secrets.token_urlsafe(36)
            s.add(ApplicationRow(application_id=body.application_id,
                                 token_hash=token_hash(token), priority=body.priority))
        return {"application_id": body.application_id, "token": token}

    def set_priority(self, app: str, priority: int):
        with self.sessions.begin() as s:
            row = s.get(ApplicationRow, app)
            if row is None:
                raise WarehouseError(404, "application not found")
            row.priority = priority

    def rotate_token(self, app: str, *, revoke: bool = False) -> str | None:
        with self.sessions.begin() as s:
            row = s.get(ApplicationRow, app, with_for_update=True)
            if row is None:
                raise WarehouseError(404, "application not found")
            token = secrets.token_urlsafe(36)
            row.token_hash = token_hash(token)
        return None if revoke else token

    def put_spec(self, app: str, spec: Specification) -> SpecificationRef:
        validate_schema(spec.json_schema)
        payload = spec.model_dump(mode="json")
        digest = digest_json(payload)
        with self.sessions.begin() as s:
            self.lock(s, f"app:{app}")
            row = s.get(SpecificationRow, (app, spec.spec_id, spec.version))
            if row and row.sha256 != digest:
                raise WarehouseError(409, "specification versions are immutable")
            if row is None:
                s.add(SpecificationRow(application_id=app, spec_id=spec.spec_id,
                                       version=spec.version, sha256=digest, payload=payload))
        return SpecificationRef(spec_id=spec.spec_id, version=spec.version, sha256=digest)

    @staticmethod
    def spec(s: Session, app: str, ref: SpecificationRef) -> Specification:
        row = s.get(SpecificationRow, (app, ref.spec_id, ref.version))
        if row is None or row.sha256 != ref.sha256:
            raise WarehouseError(422, "missing or mismatched specification reference")
        return Specification.model_validate(row.payload)

    @staticmethod
    def artifact_ref(row: ArtifactRow) -> ArtifactRef:
        return ArtifactRef(artifact_id=row.artifact_id, sha256=row.sha256,
                           size_bytes=row.size_bytes, mime_type=row.mime_type)

    @staticmethod
    def validate_artifact(s: Session, app: str, ref: ArtifactRef,
                          attempt_id: str | None = None) -> ArtifactRow:
        row = s.get(ArtifactRow, ref.artifact_id)
        if (row is None or row.application_id != app or row.sha256 != ref.sha256
                or row.size_bytes != ref.size_bytes or row.mime_type != ref.mime_type
                or (attempt_id is not None and row.attempt_id != attempt_id)):
            raise WarehouseError(422, "missing or mismatched artifact reference")
        return row

    def submit(self, app: str, packet: Packet) -> tuple[PacketRecord, bool]:
        if app != packet.application_id:
            raise WarehouseError(403, "packet application does not match credential")
        validate_schema(packet.output.json_schema)
        with self.sessions.begin() as s:
            self.lock(s, f"app:{app}")
            existing = s.scalar(select(PacketRow).where(
                PacketRow.application_id == app, PacketRow.idempotency_key == packet.idempotency_key))
            if existing:
                if existing.submission_hash != packet.submission_hash():
                    raise WarehouseError(409, "idempotency key already used for different content")
                return self.record(existing), True
            if s.get(PacketRow, packet.packet_id):
                raise WarehouseError(409, "packet ID already exists")
            if packet.batch_id:
                batch = s.get(BatchRow, (app, packet.batch_id))
                if batch and batch.sealed:
                    raise WarehouseError(409, "batch is sealed")
                if batch is None:
                    s.add(BatchRow(application_id=app, batch_id=packet.batch_id))
                peers = s.scalars(select(PacketRow.payload).where(PacketRow.application_id == app,
                                  PacketRow.batch_id == packet.batch_id)).all()
                if any(bool(peer.get("experiment")) != bool(packet.experiment) for peer in peers):
                    raise WarehouseError(409, "comparison metadata must be present on every batch packet")
            if packet.experiment:
                identity = (app, packet.experiment.dataset_id, packet.experiment.case_id)
                existing_case = s.get(ComparisonCaseRow, identity)
                source_hash = digest_json(comparison_inputs(packet))
                if existing_case and existing_case.source_hash != source_hash:
                    raise WarehouseError(409, "comparison source changed; use a new case ID")
                if existing_case is None:
                    s.add(ComparisonCaseRow(application_id=app, dataset_id=identity[1],
                                            case_id=identity[2], source_hash=source_hash))
                for payload in peers:
                    experiment = payload.get("experiment")
                    if not experiment or experiment["dataset_id"] != packet.experiment.dataset_id:
                        raise WarehouseError(409, "comparison batches require one dataset")
                    if experiment["case_id"] == packet.experiment.case_id:
                        raise WarehouseError(409, "comparison case already exists in batch")
            for ref in packet.specifications:
                self.spec(s, app, ref)
            if packet.output.schema_ref:
                if self.spec(s, app, packet.output.schema_ref).json_schema is None:
                    raise WarehouseError(422, "schema reference contains no JSON Schema")
            for material in packet.materials:
                if material.artifact:
                    self.validate_artifact(s, app, material.artifact)
            row = PacketRow(packet_id=packet.packet_id, application_id=app,
                idempotency_key=packet.idempotency_key, submission_hash=packet.submission_hash(),
                batch_id=packet.batch_id, payload=packet.model_dump(mode="json"), status="queued",
                priority=packet.priority, live=packet.live, created_at=packet.created_at,
                not_before=packet.not_before, deadline=packet.deadline, attempts=0,
                fencing_token=0, acknowledged=False)
            s.add(row)
            s.flush()
            return self.record(row), False

    @staticmethod
    def record(row: PacketRow) -> PacketRecord:
        return PacketRecord(packet=Packet.model_validate(row.payload), status=row.status,
                            attempts=row.attempts, acknowledged=row.acknowledged,
                            result=row.result, error=row.error)

    @staticmethod
    def owned(s: Session, app: str, packet_id: str, *, lock=False) -> PacketRow:
        query = select(PacketRow).where(PacketRow.packet_id == packet_id,
                                        PacketRow.application_id == app)
        if lock:
            query = query.with_for_update()
        row = s.scalar(query)
        if row is None:
            raise WarehouseError(404, "packet not found")
        return row

    def get_packet(self, app: str, packet_id: str) -> PacketRecord:
        with self.sessions() as s:
            return self.record(self.owned(s, app, packet_id))

    def cancel(self, app: str, packet_id: str) -> PacketRecord:
        with self.sessions.begin() as s:
            row = self.owned(s, app, packet_id, lock=True)
            if row.status in {"succeeded", "failed"}:
                raise WarehouseError(409, "packet is already terminal")
            if row.status != "cancelled":
                row.status, row.completed_at = "cancelled", utcnow()
                row.error = ErrorInfo(code="cancelled", message="cancelled by application").model_dump()
                if row.current_attempt_id:
                    attempt = s.get(AttemptRow, row.current_attempt_id)
                    attempt.status = "cancelled"
            return self.record(row)

    def results(self, app: str, limit: int = 100, batch_id: str | None = None) -> list[ResultEnvelope]:
        with self.sessions() as s:
            query = select(PacketRow).where(PacketRow.application_id == app,
                PacketRow.status.in_(["succeeded", "failed", "cancelled"]),
                PacketRow.acknowledged.is_(False))
            if batch_id is not None:
                query = query.where(PacketRow.batch_id == batch_id)
            rows = s.scalars(query.order_by(PacketRow.completed_at, PacketRow.packet_id).limit(limit))
            return [self.envelope(s, row) for row in rows]

    @staticmethod
    def envelope(s: Session, row: PacketRow) -> ResultEnvelope:
        attempt = s.get(AttemptRow, row.current_attempt_id) if row.current_attempt_id else None
        return ResultEnvelope(packet=row.payload, status=row.status, result=row.result,
            error=row.error, completed_at=aware(row.completed_at),
            attempt_id=row.current_attempt_id,
            factory_id=attempt.factory_id if attempt else None,
            profile_id=attempt.assignment["profile"]["profile_id"] if attempt else None)

    def acknowledge(self, app: str, packet_id: str):
        with self.sessions.begin() as s:
            row = self.owned(s, app, packet_id, lock=True)
            if row.status not in {"succeeded", "failed", "cancelled"}:
                raise WarehouseError(409, "packet is not terminal")
            row.acknowledged = True

    def batch(self, app: str, batch_id: str, *, seal=False) -> dict:
        with self.sessions.begin() as s:
            self.lock(s, f"app:{app}")
            batch = s.get(BatchRow, (app, batch_id))
            if batch is None:
                raise WarehouseError(404, "batch not found")
            if seal:
                batch.sealed = True
            rows = s.scalars(select(PacketRow.status).where(
                PacketRow.application_id == app, PacketRow.batch_id == batch_id)).all()
            return {"batch_id": batch_id, "sealed": batch.sealed, "total": len(rows),
                    "terminal": sum(r in {"succeeded", "failed", "cancelled"} for r in rows),
                    "experiment_export": {"state": batch.export_state,
                                          "response": batch.export_response}}

    def reset_export(self, app: str, batch_id: str):
        """Operator recovery only after independently confirming remote experiment absence."""
        with self.sessions.begin() as s:
            batch = s.get(BatchRow, (app, batch_id), with_for_update=True)
            if batch is None:
                raise WarehouseError(404, "batch not found")
            if batch.export_state not in {"uploading", "uncertain"}:
                raise WarehouseError(409, "only uncertain exports can be reset")
            batch.export_state = "pending"

    def expire(self, s: Session, now: datetime):
        candidates = s.execute(select(AttemptRow.attempt_id, AttemptRow.packet_id).where(
            AttemptRow.status == "leased", AttemptRow.lease_expires_at <= now)).all()
        for attempt_id, packet_id in candidates:
            # All attempt mutations take packet -> attempt locks, including cancellation.
            packet = s.scalar(select(PacketRow).where(PacketRow.packet_id == packet_id)
                              .with_for_update(skip_locked=True))
            if packet is None or packet.status != "leased" or packet.current_attempt_id != attempt_id:
                continue
            attempt = s.scalar(select(AttemptRow).where(AttemptRow.attempt_id == attempt_id)
                               .with_for_update().execution_options(populate_existing=True))
            if attempt.status != "leased" or aware(attempt.lease_expires_at) > now:
                continue
            attempt.status = "expired"
            packet.error = ErrorInfo(code="lease_expired", message="factory lease expired",
                                     retryable=packet.attempts < self.settings.max_attempts).model_dump()
            if packet.attempts >= self.settings.max_attempts:
                packet.status, packet.completed_at = "failed", now
            else:
                packet.status, packet.not_before = "queued", now
        s.flush()

    def reap_expired(self):
        with self.sessions.begin() as s:
            self.expire(s, utcnow())

    def resolved_assignment(self, s: Session, attempt: AttemptRow, row: PacketRow) -> Assignment:
        packet = Packet.model_validate(row.payload)
        specs = [self.spec(s, row.application_id, ref) for ref in packet.specifications]
        schema = packet.output.json_schema
        if packet.output.schema_ref:
            schema = self.spec(s, row.application_id, packet.output.schema_ref).json_schema
        return Assignment(**{**attempt.assignment, "lease_expires_at": aware(attempt.lease_expires_at)},
            packet=packet,
            resolved_system_prompt="\n\n".join(x for x in [
                *[sp.system_prompt for sp in specs], packet.request.system_prompt] if x),
            resolved_instructions="\n\n".join(x for x in [
                *[sp.instructions for sp in specs], packet.request.instructions] if x),
            resolved_output_schema=schema)

    def claim(self, request: ClaimRequest) -> Assignment | None:
        now = utcnow()
        with self.sessions.begin() as s:
            self.lock(s, f"factory:{request.factory_id}")
            self.expire(s, now)
            counts = dict(s.execute(select(AttemptRow.resource_group, func.count()).where(
                AttemptRow.factory_id == request.factory_id, AttemptRow.status == "leased",
                AttemptRow.lease_expires_at > now).group_by(AttemptRow.resource_group)).all())
            profiles = [p for p in request.profiles if counts.get(p.resource_group, 0)
                        < request.resource_groups.get(p.resource_group, 0)]
            if not profiles:
                return None
            candidates = s.scalars(select(PacketRow).join(ApplicationRow).where(
                PacketRow.status == "queued", PacketRow.attempts < self.settings.max_attempts,
                or_(PacketRow.not_before.is_(None), PacketRow.not_before <= now)
            ).order_by(case((PacketRow.priority == 0, 1), else_=0), PacketRow.live.desc(),
                (ApplicationRow.priority + PacketRow.priority).desc(),
                PacketRow.deadline.asc().nullslast(), PacketRow.created_at, PacketRow.packet_id)).all()
            for candidate in candidates:
                packet = Packet.model_validate(candidate.payload)
                profile = next((p for p in profiles if p.matches(packet)), None)
                if profile is None:
                    continue
                row = s.scalar(select(PacketRow).where(PacketRow.packet_id == candidate.packet_id,
                    PacketRow.status == "queued", PacketRow.attempts < self.settings.max_attempts,
                    or_(PacketRow.not_before.is_(None), PacketRow.not_before <= now))
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True))
                if row is None:
                    continue
                specs = [self.spec(s, row.application_id, ref) for ref in packet.specifications]
                schema = packet.output.json_schema
                if packet.output.schema_ref:
                    schema = self.spec(s, row.application_id, packet.output.schema_ref).json_schema
                row.fencing_token += 1
                row.attempts += 1
                row.status, row.error = "leased", None
                row.current_attempt_id = new_id()
                assignment = Assignment(packet=packet, attempt_id=row.current_attempt_id,
                    factory_id=request.factory_id, fencing_token=row.fencing_token,
                    lease_expires_at=now + timedelta(seconds=self.settings.lease_seconds),
                    profile=profile,
                    resolved_system_prompt="\n\n".join(
                        x for x in [*[sp.system_prompt for sp in specs], packet.request.system_prompt] if x),
                    resolved_instructions="\n\n".join(
                        x for x in [*[sp.instructions for sp in specs], packet.request.instructions] if x),
                    resolved_output_schema=schema)
                s.add(AttemptRow(attempt_id=assignment.attempt_id, packet_id=row.packet_id,
                    factory_id=request.factory_id, resource_group=profile.resource_group,
                    fencing_token=row.fencing_token, lease_expires_at=assignment.lease_expires_at,
                    status="leased", assignment=assignment.model_dump(mode="json", exclude={
                        "packet", "resolved_system_prompt", "resolved_instructions", "resolved_output_schema"})))
                return assignment
            return None

    @staticmethod
    def current(s: Session, attempt_id: str, token: AttemptToken, *, replay=False):
        attempt = s.get(AttemptRow, attempt_id)
        if (attempt is None or attempt.factory_id != token.factory_id
                or attempt.fencing_token != token.fencing_token):
            raise WarehouseError(409, "stale or unauthorized attempt")
        packet = s.scalar(select(PacketRow).where(PacketRow.packet_id == attempt.packet_id)
                          .with_for_update())
        attempt = s.scalar(select(AttemptRow).where(AttemptRow.attempt_id == attempt_id)
                           .with_for_update().execution_options(populate_existing=True))
        if attempt.factory_id != token.factory_id or attempt.fencing_token != token.fencing_token:
            raise WarehouseError(409, "stale or unauthorized attempt")
        if replay and attempt.status == "succeeded" and packet.status == "succeeded":
            return attempt, packet
        if (attempt.status != "leased" or aware(attempt.lease_expires_at) <= utcnow()
                or packet.status != "leased" or packet.current_attempt_id != attempt_id):
            raise WarehouseError(409, "attempt lease is no longer current")
        return attempt, packet

    def assignment(self, attempt_id: str, token: AttemptToken) -> Assignment:
        with self.sessions.begin() as s:
            attempt, row = self.current(s, attempt_id, token)
            return self.resolved_assignment(s, attempt, row)

    def renew(self, attempt_id: str, token: AttemptToken) -> datetime:
        with self.sessions.begin() as s:
            attempt, _ = self.current(s, attempt_id, token)
            attempt.lease_expires_at = utcnow() + timedelta(seconds=self.settings.lease_seconds)
            return attempt.lease_expires_at

    def complete(self, attempt_id: str, body: Completion) -> PacketRecord:
        digest = digest_json(body.result.model_dump(mode="json"))
        with self.sessions.begin() as s:
            attempt, row = self.current(s, attempt_id, body, replay=True)
            if attempt.status == "succeeded":
                if digest != attempt.result_hash:
                    raise WarehouseError(409, "accepted completion differs from replay")
                return self.record(row)
            assignment = self.resolved_assignment(s, attempt, row)
            output = assignment.packet.output
            if output.kind == "text" and (body.result.text is None or not body.result.text.strip()):
                raise WarehouseError(422, "text output must contain nonblank text")
            if output.kind == "json" and assignment.resolved_output_schema:
                try:
                    jsonschema.Draft202012Validator(assignment.resolved_output_schema).validate(
                        body.result.json_data)
                except (jsonschema.ValidationError, Unresolvable, RecursionError) as exc:
                    raise WarehouseError(422, "output does not satisfy JSON Schema") from exc
            for ref in body.result.artifacts:
                self.validate_artifact(s, row.application_id, ref, attempt_id)
            if output.kind in {"image", "audio", "video"}:
                if not any(a.mime_type.startswith(output.kind + "/") and a.size_bytes > 0
                           and (output.mime_type is None or a.mime_type == output.mime_type)
                           for a in body.result.artifacts):
                    raise WarehouseError(422, "media output requires a matching nonempty artifact")
            attempt.status, attempt.result_hash = "succeeded", digest
            row.status, row.completed_at = "succeeded", utcnow()
            row.result, row.error = body.result.model_dump(mode="json"), None
            return self.record(row)

    def fail(self, attempt_id: str, body: Failure) -> PacketRecord:
        with self.sessions.begin() as s:
            attempt, row = self.current(s, attempt_id, body)
            attempt.status = "failed"
            row.error = body.error.model_dump(mode="json")
            if body.error.retryable and row.attempts < self.settings.max_attempts:
                row.status = "queued"
                row.not_before = utcnow() + timedelta(seconds=min(60, 2 ** row.attempts))
            else:
                row.status, row.completed_at = "failed", utcnow()
            return self.record(row)

    def demand(self) -> list[Packet]:
        with self.sessions.begin() as s:
            self.expire(s, utcnow())
            return [Packet.model_validate(payload) for payload in s.scalars(select(PacketRow.payload)
                .where(PacketRow.status == "queued", PacketRow.priority > 0,
                       or_(PacketRow.not_before.is_(None), PacketRow.not_before <= utcnow())))]

    def activity(self, factory_id: str) -> int:
        with self.sessions() as s:
            return s.scalar(select(func.count()).select_from(AttemptRow).where(
                AttemptRow.factory_id == factory_id, AttemptRow.status == "leased",
                AttemptRow.lease_expires_at > utcnow()))

    def artifact(self, app: str, artifact_id: str) -> ArtifactRow:
        with self.sessions() as s:
            row = s.get(ArtifactRow, artifact_id)
            if row is None or row.application_id != app:
                raise WarehouseError(404, "artifact not found")
            return row

    def input_artifact(self, attempt_id: str, token: AttemptToken, artifact_id: str) -> ArtifactRow:
        with self.sessions.begin() as s:
            _, row = self.current(s, attempt_id, token)
            packet = Packet.model_validate(row.payload)
            allowed = {m.artifact.artifact_id for m in packet.materials if m.artifact}
            if artifact_id not in allowed:
                raise WarehouseError(404, "artifact is not an input of this attempt")
            artifact = s.get(ArtifactRow, artifact_id)
            if artifact is None or artifact.application_id != row.application_id:
                raise WarehouseError(404, "artifact not found")
            return artifact

    def save_artifact(self, app: str | None, artifact_id: str, digest: str, size: int,
                      mime_type: str, path: str, *, attempt_id=None, token=None) -> ArtifactRef:
        with self.sessions.begin() as s:
            if attempt_id:
                _, row = self.current(s, attempt_id, token)
                app = row.application_id
            row = ArtifactRow(artifact_id=artifact_id, application_id=app, attempt_id=attempt_id,
                sha256=digest, size_bytes=size, mime_type=mime_type, path=path)
            s.add(row)
            return self.artifact_ref(row)
