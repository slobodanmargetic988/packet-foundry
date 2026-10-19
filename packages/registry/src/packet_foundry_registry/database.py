"""Registry-owned persistence and nonblocking PostgreSQL coordination locks."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Factory(Base):
    __tablename__ = "registry_factories"

    factory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    draining: Mapped[bool] = mapped_column(Boolean, default=False)
    registration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RegistryIdentity(Base):
    __tablename__ = "registry_identity"

    singleton: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), unique=True)


class CloudPod(Base):
    __tablename__ = "registry_cloud_pods"

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(String(128), index=True)
    factory_id: Mapped[str] = mapped_column(ForeignKey("registry_factories.factory_id"), unique=True)
    pod_name: Mapped[str] = mapped_column(String(200), unique=True)
    provider_pod_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    state: Mapped[str] = mapped_column(String(32))
    shutdown_action: Mapped[str] = mapped_column(String(16), default="stop")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_busy_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def make_engine(url: str) -> Engine:
    options: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool

            options["poolclass"] = StaticPool
    else:
        # Advisory locks span async network I/O. Never let a synchronous pool
        # checkout wait and block the event loop that must release another lock.
        options["pool_timeout"] = 0
        options["connect_args"] = {"connect_timeout": 5}
    return create_engine(url, **options)


def sessions(engine: Engine):
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def advisory_lock(engine: Engine, namespace: str) -> Iterator[bool]:
    """Try rather than block: never block the event loop waiting for another router."""
    if engine.dialect.name != "postgresql":
        # SQLite is deliberately limited to isolated nonconcurrent unit tests.
        yield True
        return
    key = int.from_bytes(hashlib.sha256(namespace.encode()).digest()[:8], signed=True)
    with engine.connect() as connection:
        acquired = bool(connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}))
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def migrate(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()
