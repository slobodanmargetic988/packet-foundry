"""Registry configuration. Credentials are resolved from environment, never template JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from packet_foundry_contracts import ExecutionProfile, FactoryRegistration, SafeId, WireModel
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunpodTemplate(WireModel):
    template_id: SafeId
    enabled: bool = True
    profiles: list[ExecutionProfile] = Field(min_length=1)
    resource_groups: dict[str, Annotated[int, Field(ge=1, le=256)]] = Field(
        default_factory=lambda: {"default": 1}
    )
    pod_parameters: dict = Field(default_factory=dict)
    factory_config: dict = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    environment_from: dict[str, str] = Field(default_factory=dict)
    max_factories: int = Field(default=1, ge=1, le=256)
    startup_timeout_seconds: int = Field(default=600, ge=30)
    idle_timeout_seconds: int = Field(default=300, ge=1)
    shutdown_action: Literal["stop", "terminate"] = "stop"

    @model_validator(mode="after")
    def valid_configuration(self) -> RunpodTemplate:
        FactoryRegistration(
            factory_id="validation", name="validation", profiles=self.profiles,
            resource_groups=self.resource_groups,
        )
        if not (self.pod_parameters.get("imageName") or self.pod_parameters.get("templateId")):
            raise ValueError("Runpod pod_parameters requires imageName or templateId")
        if {"env", "name"} & self.pod_parameters.keys():
            raise ValueError("Runpod name/env are managed; use environment/environment_from")
        if any(key.startswith("FOUNDRY_FACTORY_") for key in self.environment_from):
            raise ValueError("Factory bootstrap environment is managed by registry")
        if {"registry_token", "token", "api_key"} & self.factory_config.keys():
            raise ValueError("factory_config must contain references, never credentials")
        if {"registry_token_env", "registry_token_file"} & self.factory_config.keys():
            raise ValueError("factory bootstrap credential reference is managed by registry")
        backends = self.factory_config.get("backends", {})
        if not isinstance(backends, dict) or any(profile.backend not in backends for profile in self.profiles):
            raise ValueError("factory_config.backends must configure every advertised backend")
        return self


class RegistrySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FOUNDRY_REGISTRY_", extra="forbid",
                                      hide_input_in_errors=True)

    database_url: str = "postgresql+psycopg://foundry_registry@localhost/foundry_registry"
    admin_token: SecretStr = SecretStr("")
    warehouse_token: SecretStr = SecretStr("")
    warehouse_url: str = "http://127.0.0.1:8101"
    public_url: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8102, ge=1, le=65535)
    offline_after_seconds: int = Field(default=90, ge=30)
    warehouse_timeout_seconds: float = Field(default=30, gt=0)
    poll_interval_seconds: float = Field(default=0.5, gt=0)
    max_manifest_bytes: int = Field(default=4 * 1024**2, ge=1024)
    runpod_enabled: bool = False
    runpod_api_key: SecretStr = SecretStr("")
    runpod_api_url: str = "https://rest.runpod.io/v1"
    runpod_poll_seconds: float = Field(default=15, gt=0)
    runpod_templates: list[RunpodTemplate] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_templates(self) -> RegistrySettings:
        credentials = [self.admin_token.get_secret_value(), self.warehouse_token.get_secret_value()]
        if (any(value and len(value) < 32 for value in credentials)
                or (all(credentials) and credentials[0] == credentials[1])):
            raise ValueError("distinct admin/service tokens of at least 32 characters are required")
        for value in (self.warehouse_url, self.runpod_api_url, self.public_url):
            if value is None:
                continue
            parsed = urlsplit(value)
            if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("Service URLs must be HTTP(S) without embedded credentials/query/fragment")
        ids = [t.template_id for t in self.runpod_templates]
        if len(ids) != len(set(ids)):
            raise ValueError("Runpod template IDs must be unique")
        if self.runpod_enabled and not self.public_url:
            raise ValueError("public_url is required for Runpod factories")
        return self


def load_settings(path: Path | None = None) -> RegistrySettings:
    if path is None:
        return RegistrySettings()
    values = json.loads(path.read_text(encoding="utf-8"))
    if {"admin_token", "warehouse_token", "runpod_api_key"} & values.keys():
        raise ValueError("Registry credential values must be configured through environment")
    return RegistrySettings(**values)
