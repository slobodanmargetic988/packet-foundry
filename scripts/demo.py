#!/usr/bin/env python3
"""Cost-free demonstration. The fixture provider never invokes a real model."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4


class SyntheticProvider(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        pass  # Never log prompts, authorization headers, or request bodies.

    def respond(self, code: int, payload: dict) -> None:
        content = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.respond(200, {"status": "ok", "synthetic": True})
        else:
            self.respond(404, {"detail": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.respond(404, {"detail": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4 * 1024 * 1024:
                self.respond(413, {"detail": "unsupported request size"})
                return
            body = json.loads(self.rfile.read(length))
            signature = hashlib.sha256(json.dumps(body.get("messages"), sort_keys=True).encode()).hexdigest()
            content = (json.dumps({"synthetic": True, "request_sha256": signature})
                       if body.get("response_format")
                       else f"Synthetic demo output: {signature[:16]}. No model was called.")
            self.respond(200, {
                "id": "demo-" + signature[:16], "object": "chat.completion",
                "model": body.get("model", "deterministic-demo"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
        except (ValueError, TypeError):
            self.respond(400, {"detail": "invalid request"})


def demonstrate(directory: Path, warehouse_url: str, timeout: float) -> None:
    from bootstrap import NoRedirect, read_env

    token = read_env(directory / "collector.env").get("FOUNDRY_APPLICATION_TOKEN")
    if not token:
        raise RuntimeError("run bootstrap enroll before the demonstration")
    packet_id = str(uuid4())
    manifest = {"packet": {
        "packet_id": packet_id, "idempotency_key": "demo-" + packet_id,
        "request": {"instructions": "Produce one concise demonstration output.",
                    "model": "deterministic-demo"},
        "materials": [{"kind": "text", "text": "Synthetic Packet Foundry reference material."}],
        "output": {"kind": "text"},
        "metadata": {"synthetic_demo": True},
    }}
    input_dir = directory / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    pending = input_dir / f".{packet_id}.tmp"
    pending.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    pending.replace(input_dir / f"demo-{packet_id}.json")
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        request = urllib.request.Request(
            warehouse_url.rstrip("/") + "/v1/packets/" + packet_id,
            headers={"Authorization": "Bearer " + token},
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
                record = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise RuntimeError(f"warehouse returned HTTP {exc.code}") from None
        except urllib.error.URLError:
            pass
        else:
            if record["status"] in {"failed", "cancelled"}:
                raise RuntimeError("demo packet reached an unsuccessful terminal state")
            if record["status"] == "succeeded" and record.get("acknowledged"):
                print(json.dumps({"packet_id": packet_id, "status": "succeeded",
                                  "collector_acknowledged": True, "synthetic": True,
                                  "result": record.get("result")}, indent=2))
                return
        time.sleep(0.5)
    raise RuntimeError("demo timed out; inspect collector/factory/registry service logs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-provider", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent.parent / ".local/demo")
    parser.add_argument("--warehouse-url", default="http://127.0.0.1:8101")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if args.serve_provider:
        with ThreadingHTTPServer((args.host, args.port), SyntheticProvider) as server:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    else:
        try:
            demonstrate(args.directory, args.warehouse_url, args.timeout)
        except (OSError, ValueError, RuntimeError) as exc:
            message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            parser.exit(1, f"Demonstration failed: {message}\n")


if __name__ == "__main__":
    main()
