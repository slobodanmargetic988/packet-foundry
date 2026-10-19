"""Versioned wire contracts. No service or provider dependencies belong here."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

SafeId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_:-][A-Za-z0-9_.:-]*$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
OutputKind = Literal["text", "json", "image", "audio", "video"]
InputKind = Literal["text", "json", "image", "audio", "video", "document"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)


class ArtifactRef(WireModel):
    artifact_id: SafeId
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    mime_type: str = Field(min_length=1, max_length=150)
    filename: str | None = Field(default=None, max_length=255)


class Specification(WireModel):
    spec_id: SafeId
    version: SafeId
    system_prompt: str = ""
    instructions: str = ""
    json_schema: dict[str, JsonValue] | None = None


class SpecificationRef(WireModel):
    spec_id: SafeId
    version: SafeId
    sha256: Sha256


class Material(WireModel):
    kind: InputKind = "text"
    name: str = "input"
    text: str | None = None
    json_data: JsonValue = None
    artifact: ArtifactRef | None = None
    source: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def content_present(self) -> Material:
        if self.kind == "text" and self.text is None:
            raise ValueError("text material requires text")
        if self.kind not in {"text", "json"} and self.artifact is None:
            raise ValueError("binary material requires an artifact reference")
        return self


class OutputSpec(WireModel):
    kind: OutputKind = "text"
    mime_type: str | None = None
    json_schema: dict[str, JsonValue] | None = None
    schema_ref: SpecificationRef | None = None

    @model_validator(mode="after")
    def one_schema(self) -> OutputSpec:
        if self.json_schema is not None and self.schema_ref is not None:
            raise ValueError("provide json_schema or schema_ref, not both")
        if (self.json_schema is not None or self.schema_ref) and self.kind != "json":
            raise ValueError("JSON schema requires kind=json")
        return self


class GenerationRequest(WireModel):
    operation: SafeId = "generate"
    model: str | None = Field(default=None, min_length=1, max_length=256)
    template: SafeId | None = None
    instructions: str = Field(min_length=1)
    system_prompt: str = ""
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class ExperimentMetadata(WireModel):
    dataset_id: SafeId
    case_id: SafeId
    label: str = ""
    tags: list[str] = Field(default_factory=list)


class Packet(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    packet_id: SafeId = Field(default_factory=new_id)
    application_id: SafeId
    user_ref: str | None = None
    correlation_id: str | None = None
    batch_id: SafeId | None = None
    idempotency_key: str = Field(min_length=1, max_length=256)
    created_at: AwareDatetime = Field(default_factory=utcnow)
    priority: int = Field(default=50, ge=0, le=100)
    live: bool = False
    not_before: AwareDatetime | None = None
    deadline: AwareDatetime | None = None
    request: GenerationRequest
    specifications: list[SpecificationRef] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    output: OutputSpec = Field(default_factory=OutputSpec)
    experiment: ExperimentMetadata | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_experiment(self) -> Packet:
        if self.live and self.priority == 0:
            raise ValueError("idle-only experiments cannot be live")
        if self.experiment and not self.batch_id:
            raise ValueError("experiment metadata requires batch_id")
        return self

    def submission_hash(self) -> str:
        return digest_json(self.model_dump(mode="json", exclude={"packet_id", "created_at"}))


class ExecutionProfile(WireModel):
    profile_id: SafeId
    model: str = Field(min_length=1, max_length=256)
    backend: SafeId
    operations: list[SafeId] = Field(default_factory=lambda: ["generate"], min_length=1)
    output_kinds: list[OutputKind] = Field(default_factory=lambda: ["text", "json"], min_length=1)
    input_kinds: list[InputKind] = Field(default_factory=lambda: ["text", "json"])
    resource_group: SafeId = "default"
    supports_live: bool = True
    accept_experiments: bool = False
    templates: list[SafeId] = Field(default_factory=list)

    def matches(self, packet: Packet) -> bool:
        return (
            (packet.request.model is None or packet.request.model == self.model)
            and packet.request.operation in self.operations
            and packet.output.kind in self.output_kinds
            and all(m.kind in self.input_kinds for m in packet.materials)
            and (not packet.live or self.supports_live)
            and (packet.priority != 0 or self.accept_experiments)
            and (packet.request.template is None or packet.request.template in self.templates)
        )


class FactoryRegistration(WireModel):
    factory_id: SafeId
    name: str = Field(min_length=1, max_length=200)
    profiles: list[ExecutionProfile] = Field(min_length=1)
    resource_groups: dict[SafeId, Annotated[int, Field(ge=1, le=256)]] = Field(
        default_factory=lambda: {"default": 1}
    )

    @model_validator(mode="after")
    def resources_exist(self) -> FactoryRegistration:
        if len({p.profile_id for p in self.profiles}) != len(self.profiles):
            raise ValueError("profile IDs must be unique")
        if any(p.resource_group not in self.resource_groups for p in self.profiles):
            raise ValueError("all profile resource groups must be configured")
        return self


class ClaimRequest(WireModel):
    factory_id: SafeId
    profiles: list[ExecutionProfile]
    resource_groups: dict[SafeId, Annotated[int, Field(ge=1, le=256)]]


class AttemptToken(WireModel):
    factory_id: SafeId
    fencing_token: int = Field(ge=1)


class Assignment(WireModel):
    packet: Packet
    attempt_id: SafeId
    factory_id: SafeId
    fencing_token: int = Field(ge=1)
    lease_expires_at: AwareDatetime
    profile: ExecutionProfile
    resolved_system_prompt: str = ""
    resolved_instructions: str
    resolved_output_schema: dict[str, JsonValue] | None = None


class ResultPackage(WireModel):
    text: str | None = None
    json_data: JsonValue = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Completion(AttemptToken):
    result: ResultPackage


class ErrorInfo(WireModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(max_length=2000)
    retryable: bool = False


class Failure(AttemptToken):
    error: ErrorInfo


PacketStatus = Literal["queued", "leased", "succeeded", "failed", "cancelled"]


class PacketRecord(WireModel):
    packet: Packet
    status: PacketStatus
    attempts: int = 0
    acknowledged: bool = False
    result: ResultPackage | None = None
    error: ErrorInfo | None = None


class ResultEnvelope(WireModel):
    packet: Packet
    status: Literal["succeeded", "failed", "cancelled"]
    result: ResultPackage | None = None
    error: ErrorInfo | None = None
    attempt_id: str | None = None
    factory_id: str | None = None
    profile_id: str | None = None
    completed_at: AwareDatetime


class ApplicationCreate(WireModel):
    application_id: SafeId
    priority: int = Field(default=50, ge=0, le=100)


class EnrollmentCreate(WireModel):
    factory_id: SafeId
    name: str = Field(min_length=1, max_length=200)
