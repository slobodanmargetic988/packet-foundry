"""Optional export of sealed, terminal batches; never executes generation again."""
import asyncio
import copy
import logging
from uuid import NAMESPACE_URL, uuid5

import httpx
from packet_foundry_contracts import Packet, digest_json
from sqlalchemy import select

from .comparison import comparison_inputs
from .db import BatchRow, PacketRow
from .repository import WarehouseRepository, aware

logger = logging.getLogger(__name__)


def snapshot(repo: WarehouseRepository, app: str, batch_id: str) -> dict | None:
    with repo.sessions() as s:
        rows = s.scalars(select(PacketRow).where(PacketRow.application_id == app,
            PacketRow.batch_id == batch_id).order_by(PacketRow.packet_id)).all()
        if not rows or any(r.status not in {"succeeded", "failed", "cancelled"} for r in rows):
            return None
        packets = [Packet.model_validate(r.payload) for r in rows]
        if not all(p.experiment for p in packets):
            return None
        datasets = {p.experiment.dataset_id for p in packets}
        cases = [p.experiment.case_id for p in packets]
        if len(datasets) != 1 or len(set(cases)) != len(cases):
            raise ValueError("a comparison batch needs one dataset and unique case IDs")
        dataset = next(iter(datasets))
        results = []
        for row, packet in zip(rows, packets, strict=True):
            envelope = repo.envelope(s, row)
            provenance = {"packet_id": packet.packet_id, "status": row.status,
                "request": packet.request.model_dump(mode="json"),
                "specifications": [ref.model_dump() for ref in packet.specifications],
                "factory_id": envelope.factory_id, "profile_id": envelope.profile_id,
                "attempt_id": envelope.attempt_id, "tags": packet.experiment.tags,
                "langsmith_trace_id": (row.result or {}).get("metadata", {}).get("langsmith_trace_id")}
            # Some server versions accept run_metadata but omit it from stored runs.
            # Keep provenance in supported outputs too, without altering delivery results.
            actual_outputs = copy.deepcopy(row.result or {})
            actual_outputs.setdefault("metadata", {})["foundry_provenance"] = provenance
            result = {
                "row_id": str(uuid5(NAMESPACE_URL, f"packet-foundry:{app}:{dataset}:{packet.experiment.case_id}")),
                "inputs": comparison_inputs(packet),
                "actual_outputs": actual_outputs,
                "start_time": packet.created_at.isoformat(),
                "end_time": aware(row.completed_at).isoformat(),
                "run_name": packet.experiment.label or "Packet Foundry generation",
                "run_metadata": provenance,
            }
            if row.error:
                result["error"] = row.error["message"]
            results.append(result)
        return {
            "experiment_name": f"packet-foundry:{app}:{batch_id}",
            "dataset_id": str(uuid5(NAMESPACE_URL, f"packet-foundry:{app}:{dataset}")),
            "dataset_name": f"{app}/{dataset}",
            "experiment_start_time": min(p.created_at for p in packets).isoformat(),
            "experiment_end_time": max(aware(r.completed_at) for r in rows).isoformat(),
            "experiment_metadata": {"application_id": app, "batch_id": batch_id},
            "results": results,
        }


async def export_completed(repo: WarehouseRepository, *, transport=None) -> dict:
    settings = repo.settings
    if not settings.langsmith_api_key:
        return {"enabled": False, "exported": 0, "uncertain": 0}
    headers = {"x-api-key": settings.langsmith_api_key.get_secret_value()}
    if settings.langsmith_workspace_id:
        headers["X-Tenant-ID"] = settings.langsmith_workspace_id
    tally = {"enabled": True, "exported": 0, "uncertain": 0}
    # One owner per exporter, including across API processes. Short DB transaction
    # marks each upload BEFORE I/O; no transaction is held while talking to LangSmith.
    with repo.sessions() as s:
        batches = [(b.application_id, b.batch_id) for b in s.scalars(select(BatchRow).where(
            BatchRow.sealed.is_(True), BatchRow.export_state != "exported"))]
    async with httpx.AsyncClient(base_url=settings.langsmith_endpoint.rstrip("/") + "/api/v1/",
                                 headers=headers, transport=transport, timeout=30,
                                 follow_redirects=False) as client:
        for app, batch_id in batches:
            try:
                payload = snapshot(repo, app, batch_id)
            except ValueError:
                logger.warning("Experiment batch has inconsistent comparison identities")
                continue
            if payload is None:
                continue
            digest = digest_json(payload)
            payload["experiment_metadata"]["foundry_manifest_sha256"] = digest
            with repo.sessions.begin() as s:
                repo.lock(s, f"export:{app}:{batch_id}")
                batch = s.get(BatchRow, (app, batch_id), with_for_update=True)
                if batch.export_state == "exported":
                    continue
                if batch.export_manifest and batch.export_manifest != digest:
                    logger.error("Immutable experiment export manifest changed")
                    continue
                reconcile = batch.export_state in {"uploading", "uncertain"}
                batch.export_manifest = digest
                batch.export_state = "uncertain" if reconcile else "uploading"
            try:
                if reconcile:
                    response = await client.get("sessions", params={"name": payload["experiment_name"]})
                    response.raise_for_status()
                    projects = response.json()
                    if not isinstance(projects, list) or any(not isinstance(p, dict) for p in projects):
                        raise ValueError("unexpected experiment reconciliation response")
                    matches = [p for p in projects if p.get("name") == payload["experiment_name"]
                        and ((p.get("extra") or {}).get("metadata") or {}).get("foundry_manifest_sha256") == digest]
                    if not matches:
                        tally["uncertain"] += 1
                        continue
                    result = {"experiment": matches[0], "reconciled": True}
                else:
                    response = await client.post("datasets/upload-experiment", json=payload)
                    response.raise_for_status()
                    result = response.json()
                    if not isinstance(result.get("experiment"), dict) or not isinstance(result.get("dataset"), dict):
                        raise ValueError("unexpected experiment upload response")
                with repo.sessions.begin() as s:
                    batch = s.get(BatchRow, (app, batch_id), with_for_update=True)
                    batch.export_state, batch.export_response = "exported", result
                tally["exported"] += 1
            except (httpx.HTTPError, ValueError, TypeError):
                with repo.sessions.begin() as s:
                    batch = s.get(BatchRow, (app, batch_id), with_for_update=True)
                    batch.export_state = "uncertain"
                tally["uncertain"] += 1
                logger.warning("LangSmith export uncertain; reconciliation required before resubmission")
    return tally


async def export_loop(repo: WarehouseRepository, *, transport=None):
    while True:
        try:
            await export_completed(repo, transport=transport)
        except Exception:
            logger.exception("Experiment export failed; generation and delivery continue")
        await asyncio.sleep(repo.settings.langsmith_export_interval)
