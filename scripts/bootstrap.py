#!/usr/bin/env python3
"""Generate persistent local deployment state and enroll least-privilege workers.

Uses only Python's standard library. No inference/cloud calls or secret printing.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_env(path: Path, values: dict[str, str]) -> None:
    private_write(path, "".join(f"{key}={value}\n" for key, value in sorted(values.items())))


def initial_config(path: Path, values: dict) -> None:
    if not path.exists():
        private_write(path, json.dumps(values, indent=2) + "\n")
        # Container configs contain references, not secrets; the unprivileged
        # container user must be able to read these bind-mounted files.
        if os.name != "nt":
            path.chmod(0o644)


def initialize(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    credentials = read_env(directory / "stack.env")
    for key in ("POSTGRES_PASSWORD", "FOUNDRY_WAREHOUSE_DB_PASSWORD",
                "FOUNDRY_REGISTRY_DB_PASSWORD", "FOUNDRY_WAREHOUSE_ADMIN_TOKEN",
                "FOUNDRY_WAREHOUSE_SERVICE_TOKEN", "FOUNDRY_REGISTRY_ADMIN_TOKEN"):
        credentials.setdefault(key, secrets.token_urlsafe(48))
    write_env(directory / "stack.env", credentials)
    for name in ("collector.env", "factory.env"):
        if not (directory / name).exists():
            private_write(directory / name, "")
    (directory / "input").mkdir(exist_ok=True)
    initial_config(directory / "collector.json", {
        "application_id": "demo-app", "warehouse_url": "http://warehouse:8101",
        "token_env": "FOUNDRY_APPLICATION_TOKEN", "journal": "/data/collector.sqlite3",
        "input_dir": "/inputs", "output_dir": "/data/output",
        "send_schedule": {"interval_seconds": 2},
        "collect_schedule": {"interval_seconds": 2},
    })
    initial_config(directory / "factory.json", {
        "registry_url": "http://registry:8102", "data_dir": "/data/factory",
        "registry_token_env": "FOUNDRY_FACTORY_TOKEN",
        "registration": {
            "factory_id": "demo-factory", "name": "Synthetic quickstart factory",
            "profiles": [{"profile_id": "demo-text", "model": "deterministic-demo",
                          "backend": "demo", "output_kinds": ["text", "json"],
                          "accept_experiments": True}],
            "resource_groups": {"default": 2},
        },
        "backends": {"demo": {"kind": "openai_compatible", "auth_mode": "none",
                                "base_url": "http://demo-provider:8099/v1"}},
    })
    print(f"Deployment state ready at {directory}. Existing secrets/configuration preserved.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def request(url: str, token: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
    }, method="POST")
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        return json.load(response)


def wait_ready(url: str, timeout: float = 120) -> None:
    until = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3):
                return
        except (OSError, urllib.error.URLError):
            if time.monotonic() >= until:
                raise RuntimeError("service did not become healthy before timeout") from None
            time.sleep(1)


def enroll(directory: Path, warehouse: str, registry: str) -> None:
    credentials = read_env(directory / "stack.env")
    wait_ready(warehouse)
    wait_ready(registry)
    for filename, env_name, url, admin_key, body in (
        ("collector.env", "FOUNDRY_APPLICATION_TOKEN", warehouse.rstrip("/") + "/v1/applications",
         "FOUNDRY_WAREHOUSE_ADMIN_TOKEN", {"application_id": "demo-app", "priority": 50}),
        ("factory.env", "FOUNDRY_FACTORY_TOKEN", registry.rstrip("/") + "/v1/enrollments",
         "FOUNDRY_REGISTRY_ADMIN_TOKEN", {"factory_id": "demo-factory", "name": "Synthetic quickstart factory"}),
    ):
        values = read_env(directory / filename)
        if values.get(env_name):
            continue
        try:
            response = request(url, credentials[admin_key], body)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise RuntimeError("identity already exists without its local credential; recover/rotate that credential before retrying") from None
            raise RuntimeError(f"enrollment failed (HTTP {exc.code})") from None
        values[env_name] = response["token"]
        write_env(directory / filename, values)
    print("Application and factory credentials stored privately. Worker configuration is ready.")


def install_factory(wheel_dir: Path, *, tracing: bool = False) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("install uv first: https://docs.astral.sh/uv/getting-started/installation/")
    wheels = list(wheel_dir.glob("packet_foundry_factory-*.whl"))
    if len(wheels) != 1 or not list(wheel_dir.glob("packet_foundry_contracts-*.whl")):
        raise RuntimeError("wheel directory must contain exactly one factory wheel and its contracts wheel")
    command = [uv, "tool", "install", "--python", "3.12", "--find-links", str(wheel_dir.resolve())]
    if (wheel_dir / "constraints.txt").exists():
        command += ["--constraints", str((wheel_dir / "constraints.txt").resolve())]
    if tracing:
        command += ["--with", "langsmith>=0.3,<1"]
    subprocess.run([*command, str(wheels[0].resolve())], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / ".local/demo")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    enrollment = sub.add_parser("enroll")
    enrollment.add_argument("--warehouse-url", default="http://127.0.0.1:8101")
    enrollment.add_argument("--registry-url", default="http://127.0.0.1:8102")
    installation = sub.add_parser("install-factory")
    installation.add_argument("--wheel-dir", type=Path, required=True)
    installation.add_argument("--tracing", action="store_true", help="Install optional LangSmith SDK")
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.directory)
        elif args.command == "enroll":
            enroll(args.directory, args.warehouse_url, args.registry_url)
        else:
            install_factory(args.wheel_dir, tracing=args.tracing)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        # Avoid echoing HTTP exception objects, commands or credential values.
        if isinstance(exc, RuntimeError):
            parser.exit(1, f"Bootstrap failed: {exc}\n")
        parser.exit(1, f"Bootstrap failed ({type(exc).__name__}); check local configuration.\n")


if __name__ == "__main__":
    main()
