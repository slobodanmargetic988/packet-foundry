"""Collector configuration stores environment variable names rather than secrets."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from packet_foundry_contracts import SafeId
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .adapters import CollectorAdapter, FileAdapter
from .scheduler import Schedule


class CollectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: SafeId
    warehouse_url: str
    token_env: str = Field(default="FOUNDRY_APPLICATION_TOKEN", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    journal: Path = Path("state/collector.sqlite3")
    input_dir: Path = Path("input")
    output_dir: Path = Path("output")
    send_schedule: Schedule = Field(default_factory=lambda: Schedule(interval_seconds=60))
    collect_schedule: Schedule = Field(default_factory=lambda: Schedule(interval_seconds=30))
    adapter_factory: str | None = None

    @field_validator("warehouse_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("warehouse_url must use HTTP or HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("warehouse_url must not include credentials, a query, or a fragment")
        return value.rstrip("/")

    @classmethod
    def load(cls, path: Path) -> CollectorConfig:
        config = cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        root = path.resolve().parent
        for name in ("journal", "input_dir", "output_dir"):
            value = getattr(config, name)
            setattr(config, name, (root / value).resolve() if not value.is_absolute() else value)
        return config

    def token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise ValueError(f"set the {self.token_env} environment variable")
        return token

    def adapter(self) -> CollectorAdapter:
        if self.adapter_factory:
            module_name, separator, name = self.adapter_factory.partition(":")
            if not separator or not module_name or not name:
                raise ValueError("adapter_factory must be module:callable")
            factory = getattr(importlib.import_module(module_name), name)
            return factory(self)
        return FileAdapter(self.input_dir, self.output_dir, self.application_id)
