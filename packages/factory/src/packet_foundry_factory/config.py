"""Factory-owned configuration. Credential values never belong in advertised profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from packet_foundry_contracts import FactoryRegistration
from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str
    input: str
    source: str  # prompt, instructions, system_prompt, parameter:<name>, material:<name>


class WorkflowTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow: dict
    mappings: list[Mapping]
    output_nodes: list[str] = Field(min_length=1)


class BackendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    kind: Literal[
        "openai", "anthropic", "openai_compatible", "ollama", "codex", "claude",
        "gemini_image", "veo", "openai_speech", "comfyui",
    ]
    base_url: str | None = None
    api_key_env: str | None = None
    api_key_file: Path | None = None
    auth_mode: Literal["api", "subscription", "none"] = "api"
    executable: str | None = None
    timeout_seconds: float = Field(default=600, gt=0, le=86400)
    poll_seconds: float = Field(default=5, gt=0, le=300)
    defaults: dict = Field(default_factory=dict)
    templates: dict[str, WorkflowTemplate] = Field(default_factory=dict)
    codex_home: Path | None = None
    # Only configured hosts may receive generated-file downloads with provider credentials.
    download_hosts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_config(self) -> BackendConfig:
        if self.api_key_env and self.api_key_file:
            raise ValueError("choose api_key_env or api_key_file")
        if self.auth_mode == "subscription" and self.kind not in {"codex", "claude"}:
            raise ValueError("subscription authentication is only supported by CLI backends")
        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ValueError("base_url must be an HTTP(S) URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("base_url must not contain credentials, query or fragment")
        return self

    def key(self) -> str | None:
        if self.auth_mode != "api":
            return None
        if self.api_key_env:
            value = os.environ.get(self.api_key_env, "").strip()
            if not value:
                raise ValueError(f"credential environment variable is not set: {self.api_key_env}")
            return value
        if self.api_key_file:
            value = self.api_key_file.expanduser().read_text(encoding="utf-8").strip()
            if not value:
                raise ValueError("credential file is empty")
            return value
        raise ValueError("API authentication requires a credential reference")


class TracingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    project: str = "packet-foundry"
    api_key_env: str = "LANGSMITH_API_KEY"
    endpoint: str = "https://api.smith.langchain.com"
    capture_content: bool = False
    max_attachment_bytes: int = Field(default=64 * 1024 * 1024, gt=0)


class FactoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    registry_url: str
    registry_token_env: str | None = "FOUNDRY_FACTORY_TOKEN"
    registry_token_file: Path | None = None
    registration: FactoryRegistration
    backends: dict[str, BackendConfig]
    data_dir: Path = Field(default_factory=lambda: user_data_path("packet-foundry", appauthor=False))
    heartbeat_seconds: float = Field(default=20, gt=0)
    renew_seconds: float = Field(default=30, gt=0)
    reconnect_seconds: float = Field(default=2, gt=0)
    max_artifact_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    tracing: TracingConfig = Field(default_factory=TracingConfig)

    @model_validator(mode="after")
    def check_config(self) -> FactoryConfig:
        parsed = urlparse(self.registry_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("registry_url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("registry_url must not contain credentials, query or fragment")
        if self.registry_token_file and self.registry_token_env:
            raise ValueError("choose registry_token_file or registry_token_env")
        if not self.registry_token_file and not self.registry_token_env:
            raise ValueError("a registry credential reference is required")
        for profile in self.registration.profiles:
            if profile.backend not in self.backends:
                raise ValueError(f"unknown backend: {profile.backend}")
            backend = self.backends[profile.backend]
            outputs = {"openai": {"text", "json"}, "anthropic": {"text", "json"},
                "openai_compatible": {"text", "json"}, "ollama": {"text", "json"},
                "codex": {"text", "json"}, "claude": {"text", "json"},
                "gemini_image": {"image"}, "veo": {"video"}, "openai_speech": {"audio"},
                "comfyui": {"image", "audio", "video"}}[backend.kind]
            if not set(profile.output_kinds) <= outputs:
                raise ValueError("profile output kinds exceed backend support")
            if backend.kind == "comfyui" and not set(profile.templates) <= backend.templates.keys():
                raise ValueError("ComfyUI profile templates must exist in local configuration")
        return self

    def token(self) -> str:
        if self.registry_token_file:
            value = self.registry_token_file.expanduser().read_text(encoding="utf-8").strip()
        else:
            value = os.environ.get(self.registry_token_env or "", "").strip()
        if not value:
            raise ValueError("registry credential is missing")
        return value

    @classmethod
    def load(cls, path: Path) -> FactoryConfig:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
