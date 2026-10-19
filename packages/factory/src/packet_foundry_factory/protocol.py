"""Authenticated outward-only registry client."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from packet_foundry_contracts import ArtifactRef, Assignment, AttemptToken, Completion, Failure

from .config import FactoryConfig


class RegistryClient:
    def __init__(self, config: FactoryConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client or httpx.AsyncClient(
            base_url=config.registry_url.rstrip("/") + "/", follow_redirects=False,
            headers={"Authorization": f"Bearer {config.token()}"}, timeout=40,
        )

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = await self.client.request(method, "v1/" + path.lstrip("/"), **kwargs)
        response.raise_for_status()
        return response

    async def register(self) -> None:
        await self.request("POST", "register", json=self.config.registration.model_dump(mode="json"))

    async def heartbeat(self) -> None:
        await self.request("POST", "heartbeat", json={})

    async def work(self) -> Assignment | None:
        response = await self.request("POST", "work", params={"wait_seconds": 25}, json={})
        return Assignment.model_validate(response.json()) if response.json() else None

    def token(self, assignment: Assignment) -> AttemptToken:
        return AttemptToken(factory_id=assignment.factory_id, fencing_token=assignment.fencing_token)

    async def current(self, assignment: Assignment) -> Assignment:
        response = await self.request(
            "GET", f"attempts/{assignment.attempt_id}",
            headers={"X-Fencing-Token": str(assignment.fencing_token)},
        )
        return Assignment.model_validate(response.json())

    async def renew(self, assignment: Assignment):
        response = await self.request(
            "POST", f"attempts/{assignment.attempt_id}/renew",
            json=self.token(assignment).model_dump(mode="json"),
        )
        return response.json()["lease_expires_at"]

    async def complete(self, assignment: Assignment, completion: Completion) -> None:
        await self.request("POST", f"attempts/{assignment.attempt_id}/complete",
                           json=completion.model_dump(mode="json"))

    async def fail(self, assignment: Assignment, failure: Failure) -> None:
        await self.request("POST", f"attempts/{assignment.attempt_id}/fail",
                           json=failure.model_dump(mode="json"))

    async def download(self, assignment: Assignment, artifact: ArtifactRef, path: Path) -> None:
        digest = hashlib.sha256()
        size = 0
        temporary = path.with_suffix(".partial")
        try:
            async with self.client.stream(
                "GET", f"v1/artifacts/{artifact.artifact_id}",
                params={"attempt_id": assignment.attempt_id},
                headers={"X-Fencing-Token": str(assignment.fencing_token)},
            ) as response:
                response.raise_for_status()
                with temporary.open("wb") as out:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > min(artifact.size_bytes, self.config.max_artifact_bytes):
                            raise ValueError("input artifact exceeds declared or configured size")
                        digest.update(chunk)
                        out.write(chunk)
            if size != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise ValueError("input artifact checksum or length mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    async def upload(self, assignment: Assignment, path: Path, mime_type: str) -> ArtifactRef:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if path.stat().st_size > self.config.max_artifact_bytes:
            raise ValueError("output artifact exceeds configured size")

        async def chunks():
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    yield chunk

        response = await self.request(
            "POST", "artifacts", params={"attempt_id": assignment.attempt_id}, content=chunks(),
            headers={"X-Fencing-Token": str(assignment.fencing_token),
                     "X-Content-SHA256": digest.hexdigest(), "Content-Type": mime_type},
        )
        artifact = ArtifactRef.model_validate(response.json())
        if artifact.sha256 != digest.hexdigest() or artifact.size_bytes != path.stat().st_size:
            raise ValueError("uploaded artifact reference does not match local bytes")
        return artifact

    async def close(self) -> None:
        await self.client.aclose()
