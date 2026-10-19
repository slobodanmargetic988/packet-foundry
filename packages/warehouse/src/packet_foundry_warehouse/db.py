from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApplicationRow(Base):
    __tablename__ = "applications"
    application_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    priority: Mapped[int] = mapped_column(Integer)


class SpecificationRow(Base):
    __tablename__ = "specifications"
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"), primary_key=True)
    spec_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(128), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(150))
    path: Mapped[str] = mapped_column(String(512))


class BatchRow(Base):
    __tablename__ = "batches"
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sealed: Mapped[bool] = mapped_column(Boolean, default=False)
    export_state: Mapped[str] = mapped_column(String(30), default="pending")
    export_manifest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    export_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ComparisonCaseRow(Base):
    __tablename__ = "comparison_cases"
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_hash: Mapped[str] = mapped_column(String(64))


class PacketRow(Base):
    __tablename__ = "packets"
    __table_args__ = (
        UniqueConstraint("application_id", "idempotency_key", name="uq_packet_idempotency"),
        Index("ix_packets_dispatch", "status", "not_before", "created_at"),
    )
    packet_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    submission_hash: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    priority: Mapped[int] = mapped_column(Integer)
    live: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    current_attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AttemptRow(Base):
    __tablename__ = "attempts"
    __table_args__ = (Index("ix_attempts_factory_active", "factory_id", "status", "lease_expires_at"),)
    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    packet_id: Mapped[str] = mapped_column(ForeignKey("packets.packet_id"), index=True)
    factory_id: Mapped[str] = mapped_column(String(128))
    resource_group: Mapped[str] = mapped_column(String(128))
    fencing_token: Mapped[int] = mapped_column(Integer)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="leased")
    assignment: Mapped[dict] = mapped_column(JSON)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
