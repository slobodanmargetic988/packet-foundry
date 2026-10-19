import json
import os

import pytest
from packet_foundry_contracts import ExecutionProfile, FactoryRegistration
from packet_foundry_factory.cli import app, load_config
from packet_foundry_factory.config import BackendConfig, FactoryConfig
from pydantic import ValidationError
from typer.testing import CliRunner


def config(tmp_path):
    return FactoryConfig(registry_url="https://registry.test",
        registration=FactoryRegistration(factory_id="factory", name="Example", profiles=[
            ExecutionProfile(profile_id="local", model="test", backend="local")]),
        data_dir=tmp_path, backends={"local": BackendConfig(kind="ollama", auth_mode="none")})


def test_secrets_are_references_and_missing_credentials_fail(monkeypatch):
    monkeypatch.delenv("UNSET_FACTORY_TEST_KEY", raising=False)
    backend = BackendConfig(kind="openai", api_key_env="UNSET_FACTORY_TEST_KEY")
    with pytest.raises(ValueError):
        backend.key()
    monkeypatch.setenv("UNSET_FACTORY_TEST_KEY", "fixture-value")
    assert backend.key() == "fixture-value"
    assert "fixture-value" not in backend.model_dump_json()
    with pytest.raises(ValidationError):
        BackendConfig(kind="openai", base_url="https://secret@provider.test/v1")


def test_unknown_backend_profile_and_incompatible_output_rejected(tmp_path):
    settings = config(tmp_path).model_dump()
    settings["registration"]["profiles"][0]["output_kinds"] = ["video"]
    with pytest.raises(ValidationError, match="backend support"):
        FactoryConfig.model_validate(settings)


def test_cloud_bootstrap_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("FOUNDRY_FACTORY_CONFIG_JSON", config(tmp_path).model_dump_json())
    monkeypatch.setenv("FOUNDRY_FACTORY_ID", "ephemeral-123")
    monkeypatch.setenv("FOUNDRY_FACTORY_REGISTRY_URL", "https://private.test")
    settings = load_config(None)
    assert settings.registration.factory_id == "ephemeral-123"
    assert settings.registry_url == "https://private.test"


def test_status_never_prints_secrets(tmp_path, monkeypatch):
    settings = config(tmp_path)
    path = tmp_path / "factory.json"
    path.write_text(settings.model_dump_json())
    monkeypatch.setenv("FOUNDRY_FACTORY_TOKEN", "must-not-print")
    result = CliRunner().invoke(app, ["status", "--config", str(path)])
    assert result.exit_code == 0
    assert json.loads(result.output)["factory_id"] == "factory"
    assert "must-not-print" not in result.output


def test_doctor_uses_no_provider_inference(tmp_path, monkeypatch):
    settings = config(tmp_path)
    path = tmp_path / "factory.json"
    path.write_text(settings.model_dump_json())
    monkeypatch.setenv("FOUNDRY_FACTORY_TOKEN", "fixture")
    result = CliRunner().invoke(app, ["doctor", "--config", str(path)])
    assert result.exit_code == 0 and "OK:" in result.output


def test_setup_questionnaire_writes_only_references(tmp_path):
    path = tmp_path / "factory.json"
    result = CliRunner().invoke(app, ["setup", "--config", str(path), "--registry-url",
        "http://127.0.0.1:8020", "--factory-id", "test-factory", "--name", "Test"],
        input="ollama\nlocal\nqwen3:4b\nhttp://127.0.0.1:11434\nlocal-gpu\n1\ny\nn\nn\nn\n")
    assert result.exit_code == 0, result.output
    saved = FactoryConfig.load(path)
    assert saved.registration.factory_id == "test-factory"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_windows_startup_is_one_current_user_task(tmp_path, monkeypatch):
    from packet_foundry_factory.startup import install
    path = tmp_path / "config with spaces.json"
    path.write_text("{}")
    calls = []
    monkeypatch.setattr("packet_foundry_factory.startup.subprocess.run", lambda command, **_: calls.append(command))
    install(path, platform="win32")
    assert calls[0][:3] == ["schtasks", "/Create", "/TN"]
    assert "SYSTEM" not in calls[0] and "/RU" not in calls[0]
    assert str(path) in calls[0][calls[0].index("/TR") + 1]
    assert "-AllowStartIfOnBatteries" in calls[1][-1]
    assert "-DontStopIfGoingOnBatteries" in calls[1][-1]
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in calls[1][-1]
    assert calls[2] == ["schtasks", "/Run", "/TN", "PacketFoundryFactory"]
