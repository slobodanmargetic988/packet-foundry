"""Registry operations. Issued factory credentials are printed exactly once."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from .settings import load_settings


def main():
    parser = argparse.ArgumentParser(prog="foundry-registry")
    parser.add_argument("--config", type=Path, help="Nonsecret registry settings JSON")
    parser.add_argument("--url", help="Registry URL for enrollment/list commands")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="Run the private registry HTTP service")
    commands.add_parser("migrate", help="Upgrade registry-owned database schema")
    enrollment = commands.add_parser("enroll", help="Issue a factory credential once")
    enrollment.add_argument("factory_id")
    enrollment.add_argument("--name", required=True)
    commands.add_parser("list", help="List factories without credentials")
    revoke = commands.add_parser("revoke", help="Revoke a factory credential")
    revoke.add_argument("factory_id")
    rotate = commands.add_parser("rotate", help="Replace credential; old credential stops working")
    rotate.add_argument("factory_id")
    commands.add_parser("pods", help="List owned Runpod lifecycle records")
    recover = commands.add_parser("recover-pod", help="Resume a stopped pod or resolve absent unknown create")
    recover.add_argument("record_id")
    recover.add_argument("--confirm-provider-absence", action="store_true")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        if args.command == "migrate":
            from .database import migrate

            migrate(settings.database_url)
            return
        if args.command == "run":
            import uvicorn

            from .app import create_app

            uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
            return
        if not settings.admin_token.get_secret_value():
            parser.exit(2, "Registry admin credential is not configured\n")
        url = args.url or settings.public_url or f"http://{settings.host}:{settings.port}"
        with httpx.Client(base_url=url.rstrip("/") + "/", timeout=30, follow_redirects=False,
                          headers={"Authorization": "Bearer " + settings.admin_token.get_secret_value()}) as client:
            if args.command == "enroll":
                response = client.post("v1/enrollments", json={
                    "factory_id": args.factory_id, "name": args.name,
                })
            elif args.command in {"revoke", "rotate"}:
                response = client.post(f"v1/factories/{args.factory_id}/{args.command}")
            elif args.command == "recover-pod":
                response = client.post(f"v1/runpod/pods/{args.record_id}/recover", headers={
                    "X-Confirm-Provider-Absence": str(args.confirm_provider_absence).lower(),
                })
            else:
                response = client.get("v1/runpod/pods" if args.command == "pods" else "v1/factories")
            if not response.is_success:
                parser.exit(1, f"Registry request failed (HTTP {response.status_code})\n")
            print(json.dumps(response.json(), indent=2))
    except (ValueError, OSError, httpx.HTTPError):
        # Configuration/request reprs may contain secrets: never echo them.
        parser.exit(1, "Registry operation failed; check configuration and service availability\n")
