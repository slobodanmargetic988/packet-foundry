"""Native installation and operation commands. Setup never makes inference calls."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Annotated

import httpx
import typer
from packet_foundry_contracts import ExecutionProfile, FactoryRegistration

from .config import BackendConfig, FactoryConfig, TracingConfig
from .daemon import FactoryDaemon, InstanceLock
from .journal import Journal
from .providers import ProviderError, native_executable

app = typer.Typer(no_args_is_help=True, help="Private, outbound-connected generation worker.")
ConfigOption = Annotated[Path | None, typer.Option("--config", "-c", help="Factory JSON configuration")]


def load_config(path: Path | None) -> FactoryConfig:
    if path:
        config = FactoryConfig.load(path.expanduser())
    else:
        raw = os.environ.get("FOUNDRY_FACTORY_CONFIG_JSON")
        if not raw:
            raise ValueError("provide --config or FOUNDRY_FACTORY_CONFIG_JSON")
        config = FactoryConfig.model_validate_json(raw)
    if url := os.environ.get("FOUNDRY_FACTORY_REGISTRY_URL"):
        config.registry_url = url
    if factory_id := os.environ.get("FOUNDRY_FACTORY_ID"):
        config.registration.factory_id = factory_id
    return FactoryConfig.model_validate(config.model_dump())


@app.command()
def setup(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("factory.json"),
    registry_url: Annotated[str | None, typer.Option()] = None,
    factory_id: Annotated[str | None, typer.Option()] = None,
    name: Annotated[str | None, typer.Option()] = None,
):
    """Create a local configuration through a short questionnaire."""
    if config.exists():
        raise typer.BadParameter("configuration already exists; edit it or choose another path")
    registry_url = registry_url or typer.prompt("Registry URL", default="http://127.0.0.1:8102")
    factory_id = factory_id or typer.prompt("Factory ID (use the enrolled ID)", default="my-factory")
    name = name or typer.prompt("Factory name", default=factory_id)
    backends, profiles, groups = {}, [], {}
    kinds = ["ollama", "openai", "anthropic", "openai_compatible", "codex", "claude",
             "gemini_image", "veo", "openai_speech", "comfyui"]
    while True:
        kind = typer.prompt("Backend (" + ", ".join(kinds) + ")", default="ollama")
        if kind not in kinds:
            typer.echo("Choose one of the listed backends.")
            continue
        profile_id = typer.prompt("Profile ID", default=f"{kind}-{len(profiles) + 1}")
        model = typer.prompt("Model ID", default="qwen3:4b" if kind == "ollama" else kind)
        auth = "none" if kind in {"ollama", "comfyui", "openai_compatible"} else "api"
        if kind in {"codex", "claude"}:
            auth = "subscription" if typer.confirm("Use this CLI's existing subscription login?", default=True) else "api"
        key_env = None
        if auth == "api":
            default = "ANTHROPIC_API_KEY" if kind in {"anthropic", "claude"} else "GEMINI_API_KEY" if kind in {"veo", "gemini_image"} else "OPENAI_API_KEY"
            key_env = typer.prompt("Credential environment variable name (never paste a key)", default=default)
        base = None
        if kind in {"ollama", "comfyui", "openai_compatible"}:
            default = {"ollama": "http://127.0.0.1:11434", "comfyui": "http://127.0.0.1:8188",
                       "openai_compatible": "http://127.0.0.1:8000/v1"}[kind]
            base = typer.prompt("Provider base URL", default=default)
        backends[profile_id] = BackendConfig(kind=kind, auth_mode=auth, api_key_env=key_env,
                                            base_url=base)
        outputs = {"gemini_image": ["image"], "veo": ["video"], "openai_speech": ["audio"],
                   "comfyui": ["image", "audio", "video"]}.get(kind, ["text", "json"])
        group_default = "local-gpu" if kind in {"ollama", "comfyui"} else "subscription" if auth == "subscription" else "cloud-api"
        group = typer.prompt("Resource group (share this name for profiles using the same GPU)", default=group_default)
        if group not in groups:
            groups[group] = typer.prompt("Maximum concurrent jobs in this resource group", default=1, type=int)
        live = typer.confirm("Can this profile serve live requests?", default=kind not in {"veo", "comfyui"})
        experiments = typer.confirm("Allow idle-only experimental work on this profile?", default=False)
        profiles.append(ExecutionProfile(profile_id=profile_id, backend=profile_id, model=model,
            output_kinds=outputs, supports_live=live, accept_experiments=experiments,
            resource_group=group))
        if kind == "comfyui":
            typer.echo("Add approved API workflow templates and mappings to this backend before advertising templates.")
        if not typer.confirm("Add another backend?", default=False):
            break
    tracing = TracingConfig()
    if typer.confirm("Enable optional LangSmith tracing? (requires the tracing installation extra)", default=False):
        tracing = TracingConfig(enabled=True,
            project=typer.prompt("LangSmith project", default="packet-foundry"),
            api_key_env=typer.prompt("LangSmith credential environment variable name", default="LANGSMITH_API_KEY"),
            capture_content=typer.confirm("Send source text, outputs and media attachments to LangSmith?", default=False))
    result = FactoryConfig(registry_url=registry_url,
        registration=FactoryRegistration(factory_id=factory_id, name=name, profiles=profiles,
                                          resource_groups=groups),
        backends=backends, tracing=tracing)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        config.chmod(0o600)
    typer.echo(f"Saved {config.resolve()}. Set FOUNDRY_FACTORY_TOKEN, then run doctor.")


@app.command()
def doctor(config: ConfigOption = None,
           network: Annotated[bool, typer.Option(help="Probe registry health and local providers; no inference")] = False):
    """Check configuration, referenced credentials, executables, and optional connectivity."""
    try:
        settings = load_config(config)
    except Exception:
        typer.echo("FAIL: invalid configuration; check paths and required fields")
        raise typer.Exit(1) from None
    failures = 0
    try:
        settings.token()
        typer.echo("OK: registry credential reference resolves")
    except (ValueError, OSError):
        failures += 1
        typer.echo("FAIL: registry credential reference is missing or unreadable")
    for name, backend in settings.backends.items():
        try:
            backend.key()
            if backend.kind in {"codex", "claude"}:
                binary = shutil.which(backend.executable or backend.kind)
                if not binary:
                    raise ValueError("missing executable")
                binary = native_executable(binary)
                proc = subprocess.run([binary, "exec", "--help"] if backend.kind == "codex" else [binary, "--help"],
                                      capture_output=True, text=True, timeout=10)
                required = ["--ignore-user-config", "--ephemeral"] if backend.kind == "codex" else ["--tools", "--strict-mcp-config", "--no-session-persistence"]
                if proc.returncode or any(flag not in proc.stdout for flag in required):
                    raise ValueError("CLI missing required flags")
            typer.echo(f"OK: {name} configuration and local prerequisites")
        except (ValueError, OSError, subprocess.TimeoutExpired, ProviderError):
            failures += 1
            typer.echo(f"FAIL: {name} credential or executable prerequisites; check installed CLI support")
    if settings.tracing.enabled:
        try:
            settings.tracing.key()
            import importlib.util
            if importlib.util.find_spec("langsmith") is None:
                raise ValueError("tracing extra is missing")
            typer.echo("OK: LangSmith credential reference and SDK")
        except (ValueError, OSError):
            failures += 1
            typer.echo("FAIL: LangSmith credential reference or tracing SDK is unavailable")
    if network:
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                client.get(settings.registry_url.rstrip("/") + "/health").raise_for_status()
            typer.echo("OK: registry health")
        except httpx.HTTPError:
            failures += 1
            typer.echo("FAIL: registry health is unavailable")
        for name, backend in settings.backends.items():
            if backend.kind in {"ollama", "comfyui"}:
                path = "/api/tags" if backend.kind == "ollama" else "/system_stats"
                base = backend.base_url or ("http://127.0.0.1:11434" if backend.kind == "ollama" else "http://127.0.0.1:8188")
                try:
                    httpx.get(base.rstrip("/") + path, timeout=10).raise_for_status()
                    typer.echo(f"OK: {name} responds to a readiness probe")
                except httpx.HTTPError:
                    failures += 1
                    typer.echo(f"FAIL: {name} readiness probe")
    raise typer.Exit(1 if failures else 0)


@app.command()
def run(config: ConfigOption = None):
    """Run until stopped, recovering locally journaled attempts first."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_config(config)
        settings.token()
        with InstanceLock(settings.data_dir):
            async def serve():
                daemon = FactoryDaemon(settings)
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, daemon.stop.set)
                    except (NotImplementedError, RuntimeError):
                        pass
                await daemon.run()
            asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    except Exception:
        typer.echo("Factory could not start; check configuration, credentials and the single-instance lock.")
        raise typer.Exit(1) from None


@app.command()
def status(config: ConfigOption = None):
    """Show local journal counts without printing prompts, credentials or provider results."""
    settings = load_config(config)
    journal = Journal(settings.data_dir)
    try:
        typer.echo(json.dumps({"factory_id": settings.registration.factory_id,
                               "attempts": journal.counts()}, indent=2))
    finally:
        journal.close()


@app.command("install-startup")
def install_startup(config: Annotated[Path, typer.Option("--config", "-c")]):
    """Install and start one per-user native startup task. Use credential files for services."""
    from .startup import install
    load_config(config)
    target = install(config)
    typer.echo(f"Installed per-user startup entry: {target}")


@app.command("uninstall-startup")
def uninstall_startup():
    """Remove the factory's per-user startup entry."""
    from .startup import uninstall
    uninstall()
    typer.echo("Removed factory startup entry")


def main():
    app()


if __name__ == "__main__":
    main()
