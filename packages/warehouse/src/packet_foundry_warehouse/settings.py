from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WarehouseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FOUNDRY_WAREHOUSE_", env_file=None)

    database_url: str = "postgresql+psycopg://localhost/packet_foundry_warehouse"
    admin_token: SecretStr
    service_token: SecretStr
    artifact_path: Path = Path(".local/warehouse/artifacts")
    lease_seconds: int = Field(default=120, ge=5, le=86400)
    max_attempts: int = Field(default=3, ge=1, le=100)
    max_artifact_bytes: int = Field(default=2 * 1024**3, ge=1)
    max_manifest_bytes: int = Field(default=4 * 1024**2, ge=1024)
    langsmith_api_key: SecretStr | None = None
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: str | None = None
    langsmith_export: bool = False
    langsmith_export_interval: float = Field(default=30, ge=1)

    @model_validator(mode="after")
    def credentials(self):
        values = [self.admin_token.get_secret_value(), self.service_token.get_secret_value()]
        if any(len(v) < 32 for v in values) or values[0] == values[1]:
            raise ValueError("distinct admin/service tokens of at least 32 characters are required")
        return self
