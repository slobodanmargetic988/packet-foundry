"""Deliberate real-provider smoke probe. Never runs as part of ordinary tests.

Run with uv run --all-packages python scripts/verify_live.py --help.
Evidence contains only synthetic test outputs and public execution metadata.
"""
import argparse
import asyncio
import hashlib
import json
import platform
import time
from pathlib import Path

from packet_foundry_contracts import (
    Assignment,
    ExecutionProfile,
    GenerationRequest,
    OutputSpec,
    Packet,
    utcnow,
)
from packet_foundry_factory.config import BackendConfig
from packet_foundry_factory.providers import Provider, ProviderError


async def probe(args):
    target = args.output.resolve() / f"{args.backend}-{args.kind}-{int(time.time())}"
    target.mkdir(parents=True, exist_ok=False)
    if args.backend in {"codex", "claude"}:
        config = BackendConfig(kind=args.backend, auth_mode="subscription", timeout_seconds=args.timeout)
    elif args.config:
        config = BackendConfig.model_validate_json(args.config.read_text())
        if config.kind != args.backend:
            raise ValueError("config backend does not match --backend")
    else:
        if args.backend not in {"ollama", "comfyui", "openai_compatible"} and not args.allow_api:
            raise ValueError("API probe requires --allow-api and your configured provider limits")
        keys = {"openai": "OPENAI_API_KEY", "openai_speech": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY", "gemini_image": "GEMINI_API_KEY", "veo": "GEMINI_API_KEY"}
        config = BackendConfig(kind=args.backend, base_url=args.base_url,
            auth_mode="api" if args.backend in keys else "none", api_key_env=keys.get(args.backend),
            timeout_seconds=args.timeout)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False} if args.kind == "json" else None
    instructions = {"text": "Reply exactly: packet-foundry-ok",
        "json": 'Return {"ok":true}.', "image": "A simple blue square on a white background.",
        "audio": "Packet Foundry test.",
        "video": "A stationary blue square on a white background. Static camera."}[args.kind]
    request = GenerationRequest(instructions=instructions, model=args.model,
        template=args.template, parameters=json.loads(args.parameters))
    packet = Packet(application_id="live-smoke", idempotency_key=target.name,
        request=request, output=OutputSpec(kind=args.kind, json_schema=schema))
    assignment = Assignment(packet=packet, attempt_id="live-smoke", factory_id="smoke-factory",
        fencing_token=1, lease_expires_at=utcnow(),
        profile=ExecutionProfile(profile_id="smoke", model=args.model, backend="probe", output_kinds=[args.kind]),
        resolved_instructions=instructions, resolved_output_schema=schema)
    provider = Provider(config, 256 * 1024**2)

    async def save_operation(value):
        (target / "operation.json").write_text(json.dumps(value))

    evidence = {"backend": args.backend, "model": args.model, "output_kind": args.kind,
                "platform": platform.platform(), "python": platform.python_version(),
                "started_at": utcnow().isoformat(), "real_provider": True}
    start = time.monotonic()
    try:
        output = await provider.generate(assignment, target, {}, save_operation)
        artifacts = []
        for item in output["files"]:
            path = Path(item["path"])
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024**2):
                    digest.update(chunk)
            artifacts.append({"filename": path.name, "mime_type": item["mime_type"],
                "size_bytes": path.stat().st_size, "sha256": digest.hexdigest()})
        evidence.update(status="passed", result=output["result"], artifacts=artifacts)
    except ProviderError as exc:
        evidence.update(status="failed", error_code=exc.code, message=exc.message)
    except Exception as exc:
        evidence.update(status="failed", error_code=type(exc).__name__)
    finally:
        await provider.client.aclose()
    evidence["duration_seconds"] = round(time.monotonic() - start, 3)
    evidence["finished_at"] = utcnow().isoformat()
    path = target / "evidence.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"backend": args.backend, "kind": args.kind, "status": evidence["status"],
                      "error_code": evidence.get("error_code"), "evidence": str(path)}))
    return evidence["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=["openai", "anthropic", "ollama",
        "openai_compatible", "codex", "claude", "gemini_image", "veo", "openai_speech", "comfyui"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--kind", choices=["text", "json", "image", "audio", "video"], default="text")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--template")
    parser.add_argument("--parameters", default="{}", help="JSON provider parameters")
    parser.add_argument("--allow-api", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, default=Path(".local/live-evidence"))
    raise SystemExit(0 if asyncio.run(probe(parser.parse_args())) else 1)


if __name__ == "__main__":
    main()
