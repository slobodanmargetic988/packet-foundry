"""Typed, application-scoped Warehouse API client."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
from packet_foundry_contracts import (
    ArtifactRef,
    Packet,
    PacketRecord,
    ResultEnvelope,
    SafeId,
    Specification,
    SpecificationRef,
)
from pydantic import TypeAdapter

_safe_id = TypeAdapter(SafeId)


def _path_id(value: str) -> str:
    value = _safe_id.validate_python(value)
    if value in {".", ".."}:
        raise ValueError("a path identifier cannot be a dot segment")
    return quote(value, safe="")


class WarehouseClient:
    """Owns its HTTP client unless an existing client is supplied for testing/embedding."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owned = client is None
        self.http = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            follow_redirects=False,
        )
        # Keep credentials on individual requests when embedding an existing client.
        self._headers = {"Authorization": f"Bearer {token}"}
        self.base_url = base_url.rstrip("/")

    async def __aenter__(self) -> WarehouseClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owned:
            await self.http.aclose()

    async def _json(self, method: str, path: str, **kwargs: object) -> object:
        response = await self.http.request(
            method, f"{self.base_url}/v1/{path}", headers=self._headers, **kwargs
        )
        response.raise_for_status()
        return response.json()

    async def register_specification(self, specification: Specification) -> SpecificationRef:
        return SpecificationRef.model_validate(
            await self._json("POST", "specifications", json=specification.model_dump(mode="json"))
        )

    async def submit(self, packet: Packet) -> PacketRecord:
        return PacketRecord.model_validate(
            await self._json("POST", "packets", json=packet.model_dump(mode="json"))
        )

    async def status(self, packet_id: str) -> PacketRecord:
        return PacketRecord.model_validate(await self._json("GET", f"packets/{_path_id(packet_id)}"))

    async def cancel(self, packet_id: str) -> PacketRecord:
        return PacketRecord.model_validate(await self._json("POST", f"packets/{_path_id(packet_id)}/cancel"))

    async def results(self, *, limit: int = 100, batch_id: str | None = None) -> list[ResultEnvelope]:
        params: dict[str, str | int] = {"limit": limit}
        if batch_id is not None:
            params["batch_id"] = batch_id
        raw = await self._json("GET", "results", params=params)
        if not isinstance(raw, list):
            raise ValueError("warehouse returned a non-list result response")
        return [ResultEnvelope.model_validate(item) for item in raw]

    async def acknowledge(self, packet_id: str) -> None:
        raw = await self._json("POST", f"results/{_path_id(packet_id)}/ack")
        if not isinstance(raw, dict) or raw.get("acknowledged") is not True:
            raise ValueError("warehouse did not confirm acknowledgement")

    async def seal_batch(self, batch_id: str) -> None:
        raw = await self._json("POST", f"batches/{_path_id(batch_id)}/seal")
        if not isinstance(raw, dict) or raw.get("sealed") is not True:
            raise ValueError("warehouse did not confirm batch sealing")

    async def batch(self, batch_id: str) -> dict[str, object]:
        raw = await self._json("GET", f"batches/{_path_id(batch_id)}")
        if not isinstance(raw, dict):
            raise ValueError("warehouse returned invalid batch status")
        return raw

    async def upload_artifact(self, path: Path, mime_type: str) -> ArtifactRef:
        """Hash and stream an immutable local file without loading it into memory."""
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        checksum = digest.hexdigest()

        async def chunks() -> AsyncIterator[bytes]:
            # File handles stay local to this transfer, including on cancellation.
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk

        response = await self.http.post(
            f"{self.base_url}/v1/artifacts",
            headers={
                **self._headers,
                "Content-Type": mime_type,
                "X-Content-SHA256": checksum,
            },
            content=chunks(),
        )
        response.raise_for_status()
        ref = ArtifactRef.model_validate(response.json())
        if ref.sha256 != checksum or ref.size_bytes != size:
            raise ValueError("warehouse upload checksum/size did not match the source file")
        return ref

    async def download_artifact(self, ref: ArtifactRef, destination: Path) -> Path:
        """Verify a streamed download before atomically publishing its destination."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.partial")
        digest = hashlib.sha256()
        size = 0
        try:
            async with self.http.stream(
                "GET", f"{self.base_url}/v1/artifacts/{_path_id(ref.artifact_id)}", headers=self._headers
            ) as response:
                response.raise_for_status()
                with temporary.open("xb") as stream:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > ref.size_bytes:
                            raise ValueError("artifact is larger than its manifest")
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            if size != ref.size_bytes or digest.hexdigest() != ref.sha256:
                raise ValueError("artifact checksum/size mismatch")
            os.replace(temporary, destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)
