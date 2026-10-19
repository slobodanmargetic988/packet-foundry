"""Application extension points and a useful filesystem adapter."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Protocol
from uuid import uuid4

from packet_foundry_contracts import Material, Packet, ResultEnvelope, Specification
from pydantic import BaseModel, ConfigDict, Field

from .client import WarehouseClient


@dataclass(frozen=True)
class Source:
    """A stable source key plus application-specific preparation data."""

    key: str
    value: object


class CollectorAdapter(Protocol):
    async def sources(self) -> Sequence[Source]:
        """Return available work with keys stable across process restarts."""
        ...

    async def prepare(self, source: Source, client: WarehouseClient) -> Packet:
        """Package one request; upload binary inputs and register immutable specs here."""
        ...

    async def consume(self, envelope: ResultEnvelope, client: WarehouseClient) -> None:
        """Commit application changes idempotently by packet ID; return only after commit."""
        ...


class FileMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    kind: Literal["image", "audio", "video", "document"]
    name: str = "input"
    mime_type: str | None = None


class FileManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    packet: dict[str, object]
    files: list[FileMaterial] = Field(default_factory=list)
    specification_documents: list[Specification] = Field(default_factory=list)


class FileAdapter:
    """Read JSON manifests and atomically publish verified result directories.

    Filenames in a result manifest are not trusted as filesystem paths. Output
    directories derive from packet IDs using a portable SHA-256 name.
    """

    def __init__(self, input_dir: Path, output_dir: Path, application_id: str) -> None:
        self.input_dir = input_dir.resolve()
        self.output_dir = output_dir.resolve()
        self.application_id = application_id

    async def sources(self) -> list[Source]:
        if not self.input_dir.exists():
            return []
        sources = []
        for path in sorted(self.input_dir.glob("*.json")):
            actual = path.resolve(strict=True)
            if not actual.is_relative_to(self.input_dir):
                raise ValueError("input manifest escapes the configured input directory")
            content = actual.read_bytes()
            fingerprint = hashlib.sha256(path.name.encode() + b"\0" + content).hexdigest()
            key = f"file:{fingerprint}"
            sources.append(Source(key, (actual, content)))
        return sources

    async def prepare(self, source: Source, client: WarehouseClient) -> Packet:
        path, content = source.value
        manifest = FileManifest.model_validate_json(content)
        raw = dict(manifest.packet)
        raw.setdefault("application_id", self.application_id)
        # The source fingerprint remains stable if a manifest omits an explicit key.
        raw.setdefault("idempotency_key", source.key)
        if raw["application_id"] != self.application_id:
            raise ValueError("manifest belongs to a different application")
        materials = list(raw.get("materials", []))
        for item in manifest.files:
            # A manifest must remain relative when moved between Windows and
            # POSIX hosts. Native Path.is_absolute() alone misses Windows drive
            # paths on POSIX and slash-rooted paths on Windows.
            posix = PurePosixPath(item.path)
            windows = PureWindowsPath(item.path)
            if posix.is_absolute() or windows.drive or windows.root:
                raise ValueError("file material paths must be relative on every platform")
            if ".." in posix.parts or ".." in windows.parts:
                raise ValueError("file material paths must not traverse parent directories")
            reference = Path(item.path)
            actual = (path.parent / reference).resolve(strict=True)
            if not actual.is_relative_to(self.input_dir) or not actual.is_file():
                raise ValueError("file material escapes the configured input directory")
            mime_type = item.mime_type or mimetypes.guess_type(actual.name)[0]
            artifact = await client.upload_artifact(actual, mime_type or "application/octet-stream")
            materials.append(
                Material(kind=item.kind, name=item.name, artifact=artifact).model_dump(mode="json")
            )
        raw["materials"] = materials
        specifications = list(raw.get("specifications", []))
        for document in manifest.specification_documents:
            ref = await client.register_specification(document)
            specifications.append(ref.model_dump(mode="json"))
        raw["specifications"] = specifications
        return Packet.model_validate(raw)

    def result_directory(self, packet_id: str) -> Path:
        return self.output_dir / hashlib.sha256(packet_id.encode()).hexdigest()

    async def consume(self, envelope: ResultEnvelope, client: WarehouseClient) -> None:
        destination = self.result_directory(envelope.packet.packet_id)
        serialized = envelope.model_dump(mode="json")
        if destination.exists():
            self._verify_existing(destination, serialized)
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        staging = self.output_dir / f".collect-{uuid4().hex}"
        staging.mkdir()
        try:
            downloaded: list[dict[str, str]] = []
            for index, ref in enumerate(envelope.result.artifacts if envelope.result else []):
                suffix = mimetypes.guess_extension(ref.mime_type) or ".bin"
                name = f"artifact-{index:03d}-{ref.sha256[:16]}{suffix}"
                await client.download_artifact(ref, staging / name)
                downloaded.append({"artifact_id": ref.artifact_id, "path": name})
            # The result file is the atomic application commit marker, published
            # together with all artifacts by renaming the completed directory.
            document = {"envelope": serialized, "downloaded_artifacts": downloaded}
            with (staging / "result.json").open("x", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                staging.rename(destination)
            except OSError:
                if not destination.exists():
                    raise
                self._verify_existing(destination, serialized)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _verify_existing(self, destination: Path, expected: dict[str, object]) -> None:
        if destination.is_symlink() or not destination.resolve().is_relative_to(self.output_dir):
            raise ValueError("unsafe result directory")
        document = json.loads((destination / "result.json").read_text(encoding="utf-8"))
        if document.get("envelope") != expected:
            raise ValueError("a different outcome already exists for this packet")
        refs = expected.get("result")
        artifacts = refs.get("artifacts", []) if isinstance(refs, dict) else []
        downloads = document.get("downloaded_artifacts", [])
        if len(downloads) != len(artifacts):
            raise ValueError("committed result artifact list is incomplete")
        for ref, downloaded in zip(artifacts, downloads, strict=True):
            path = destination / downloaded["path"]
            if path.is_symlink() or not path.resolve().is_relative_to(destination):
                raise ValueError("unsafe committed artifact path")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != ref["size_bytes"] or digest.hexdigest() != ref["sha256"]:
                raise ValueError("committed result artifact checksum mismatch")
