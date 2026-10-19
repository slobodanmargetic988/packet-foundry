"""Opt-in live LangSmith verification using synthetic inputs and existing local Ollama.

Run from the workspace with the factory tracing extra installed. This script creates
temporary PostgreSQL schemas and four real local generations; it never downloads a
model or calls a paid inference API. LangSmith projects/dataset remain for inspection.
Database URLs must be explicitly supplied through FOUNDRY_TEST_DATABASE_URL and
FOUNDRY_TEST_REGISTRY_DATABASE_URL (the latter may use the same test database).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from packet_foundry_contracts import (
    ArtifactRef,
    Assignment,
    ExecutionProfile,
    ExperimentMetadata,
    FactoryRegistration,
    GenerationRequest,
    Material,
    OutputSpec,
    Packet,
    ResultEnvelope,
    utcnow,
)
from packet_foundry_factory.config import BackendConfig, FactoryConfig, TracingConfig
from packet_foundry_factory.daemon import FactoryDaemon
from packet_foundry_factory.protocol import RegistryClient
from packet_foundry_factory.tracing import Tracer
from packet_foundry_registry.app import create_app as create_registry
from packet_foundry_registry.settings import RegistrySettings
from packet_foundry_warehouse.app import create_app as create_warehouse
from packet_foundry_warehouse.db import BatchRow
from packet_foundry_warehouse.experiments import export_completed
from packet_foundry_warehouse.settings import WarehouseSettings
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

ENVIRONMENT_KEYS = {
    "LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID",
    "FOUNDRY_TEST_DATABASE_URL", "FOUNDRY_TEST_REGISTRY_DATABASE_URL",
    "FOUNDRY_TEST_OLLAMA_URL",
}
VERIFICATION_STATE: dict = {}


def load_environment(path: Path | None) -> None:
    if path is None:
        return
    # Parse dotenv as data. Never source a shell file or print its values.
    from dotenv import dotenv_values

    values = dotenv_values(path.expanduser(), interpolate=False)
    for name in ENVIRONMENT_KEYS:
        if isinstance(values.get(name), str):
            os.environ[name] = values[name]


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("invalid service endpoint")
    return value.rstrip("/")


@contextlib.contextmanager
def isolated_database(value: str, prefix: str):
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise ValueError("verification requires an explicitly configured PostgreSQL test database")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    engine = create_engine(url, pool_pre_ping=True)
    schema = prefix + uuid4().hex
    created = False
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        yield url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False)
    finally:
        try:
            if created:
                with engine.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            engine.dispose()


def progress(phase: str, **counts) -> None:
    VERIFICATION_STATE.update(phase=phase, **counts)
    print(json.dumps({"phase": phase, **counts}), flush=True)


def project_summary(project, run_count: int) -> dict:
    return {"id": str(project.id), "url": project.url, "run_count": run_count}


def live_snapshot(client, trace_project_name: str, trace_ids: set[str], factory_ids: set[str],
                  experiment_names: list[str], dataset_id: str) -> dict | None:
    """Read actual persisted SDK records; no assertions based on submitted request counts."""
    trace_project = client.read_project(project_name=trace_project_name)
    traces = list(client.list_runs(project_id=trace_project.id, run_ids=list(trace_ids),
                                  is_root=True, limit=10))
    if len(traces) != 4 or {str(run.id) for run in traces} != trace_ids:
        return None
    trace_factories = Counter((run.extra or {}).get("metadata", {}).get("factory_id") for run in traces)
    if set(trace_factories) != factory_ids or sorted(trace_factories.values()) != [2, 2]:
        return None
    if any(run.end_time is None or run.error for run in traces):
        return None
    experiments = [client.read_project(project_name=name) for name in experiment_names]
    if any(str(project.reference_dataset_id) != dataset_id for project in experiments):
        return None
    actual_inputs, reference_sets, summaries = [], [], []
    for project in experiments:
        runs = list(client.list_runs(project_id=project.id, is_root=True, limit=10))
        if len(runs) != 2 or any(run.end_time is None or run.error for run in runs):
            return None
        references = {str(run.reference_example_id) for run in runs if run.reference_example_id}
        if len(references) != 2:
            return None
        # External upload implementations may omit run_metadata from native run.extra.
        # The supported actual_outputs field persists ResultPackage metadata intact.
        if any((run.outputs or {}).get("metadata", {}).get("langsmith_trace_id") not in trace_ids
               for run in runs):
            return None
        reference_sets.append(references)
        actual_inputs.append({str(run.reference_example_id): run.inputs for run in runs})
        summaries.append(project_summary(project, len(runs)))
    if reference_sets[0] != reference_sets[1] or actual_inputs[0] != actual_inputs[1]:
        return None
    examples = list(client.list_examples(dataset_id=dataset_id, limit=10))
    if {str(example.id) for example in examples} != reference_sets[0]:
        return None
    dataset = client.read_dataset(dataset_id=dataset_id)
    return {
        "status": "passed", "factory_count": len(factory_ids), "factory_run_count": len(traces),
        "comparison_batch_count": len(experiments), "experiment_run_count": 4,
        "case_count": len(examples), "trace_project": project_summary(trace_project, len(traces)),
        "experiments": summaries, "dataset": {"id": str(dataset.id), "url": dataset.url},
    }


async def verify_attachment_transport(args, endpoint: str, key: str, workspace_id: str | None,
                                      client, directory: Path, project_name: str) -> dict:
    """Upload a valid synthetic PNG through Tracer, then fetch and compare actual stored bytes."""
    directory.mkdir()
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nmU8AAAAASUVORK5CYII=")
    source, output = directory / "source.png", directory / "output.png"
    source.write_bytes(image)
    output.write_bytes(image)
    packet = Packet(application_id="synthetic-attachment-check", idempotency_key=uuid4().hex,
        request=GenerationRequest(instructions="Synthetic attachment transport; no media generation."),
        materials=[Material(name="fixture", kind="image", artifact=ArtifactRef(artifact_id="fixture",
            sha256=hashlib.sha256(image).hexdigest(), size_bytes=len(image), mime_type="image/png"))],
        output=OutputSpec(kind="image"))
    assignment = Assignment(packet=packet, attempt_id=uuid4().hex, factory_id="synthetic-transport",
        fencing_token=1, lease_expires_at=utcnow(),
        profile=ExecutionProfile(profile_id="fixture", model="synthetic-fixture", backend="fixture",
                                 input_kinds=["image"], output_kinds=["image"]),
        resolved_instructions=packet.request.instructions)
    tracer = Tracer(TracingConfig(enabled=True, project=project_name, endpoint=endpoint, capture_content=True))
    run_id = await tracer.start(assignment, {"fixture": source})
    if not run_id:
        raise RuntimeError("attachment tracing is unavailable")
    await tracer.finish(assignment, {"result": {"metadata": {"synthetic_transport_only": True}},
                                     "files": [{"path": str(output), "mime_type": "image/png"}]})
    await tracer.close()
    headers = {"x-api-key": key}
    if workspace_id:
        headers["X-Tenant-ID"] = workspace_id
    deadline = time.monotonic() + args.consistency_timeout
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as http:
        while time.monotonic() < deadline:
            try:
                # SDK read_run eagerly downloads attachments without a configurable download
                # timeout. Use the documented run response and bounded HTTP downloads here.
                response = await http.get(client.api_url.rstrip("/") + "/runs/" + run_id,
                                          headers=headers)
                response.raise_for_status()
                attachments = response.json().get("s3_urls", {})
                required = ["attachment.input_0_image", "attachment.output_0"]
                if all(name in attachments for name in required):
                    for name in required:
                        info = attachments[name]
                        url = urljoin(client.api_url.rstrip("/") + "/", info["presigned_url"])
                        parsed = urlsplit(url)
                        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                            raise ValueError("invalid attachment download URL")
                        # Never forward API credentials to an attachment's presigned host.
                        download = await http.get(url)
                        download.raise_for_status()
                        mime = info.get("mime_type") or download.headers.get("content-type", "").split(";")[0]
                        if download.content != image or mime != "image/png":
                            raise ValueError("attachment bytes or MIME differ")
                    project = await asyncio.to_thread(client.read_project, project_name=project_name)
                    return {"project": project_summary(project, 1), "run_id": run_id,
                            "uploaded_attachment_count": 2, "retrieved_attachment_count": 2,
                            "synthetic_transport_only": True, "real_media_generation": False}
            except (httpx.HTTPError, KeyError, ValueError):
                pass
            await asyncio.sleep(min(2, max(0, deadline - time.monotonic())))
    raise TimeoutError("synthetic attachment retrieval did not complete within its bound")


async def verify(args) -> dict:
    load_environment(args.env_file)
    key = os.environ.get("LANGSMITH_API_KEY", "").strip()
    warehouse_url = os.environ.get("FOUNDRY_TEST_DATABASE_URL", "").strip()
    registry_url = os.environ.get("FOUNDRY_TEST_REGISTRY_DATABASE_URL", "").strip()
    if not key or not warehouse_url or not registry_url:
        raise ValueError("LangSmith credential and both PostgreSQL test URLs must be configured")
    endpoint = validate_endpoint(os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
    ollama_url = validate_endpoint(os.environ.get("FOUNDRY_TEST_OLLAMA_URL", "http://127.0.0.1:11434"))
    from langsmith import Client

    # All inputs and remote project names are synthetic. Existing user projects are untouched.
    suffix = uuid4().hex[:12]
    application = "verify-" + suffix
    dataset = "comparison-" + suffix
    batches = ["variant-a", "variant-b"]
    trace_project_name = "packet-foundry-verification-" + suffix
    expected_experiment_names = [f"packet-foundry:{application}:{batch}" for batch in batches]
    workspace_id = os.environ.get("LANGSMITH_WORKSPACE_ID") or None
    client = Client(api_key=key, api_url=endpoint, workspace_id=workspace_id, timeout_ms=5000)
    admin, registry_admin, service = [secrets.token_urlsafe(36) for _ in range(3)]
    factory_ids = {"factory-text-" + suffix, "factory-json-" + suffix}
    trace_ids = set()
    progress("readiness")
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as readiness:
        response = await readiness.get(ollama_url + "/api/tags")
        response.raise_for_status()
        available = {entry.get("name") for entry in response.json().get("models", [])}
        if args.model not in available:
            raise ValueError("requested local model is not already installed; no download was attempted")
    with contextlib.ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="foundry-langsmith-")))
        warehouse_database = stack.enter_context(isolated_database(warehouse_url, "verify_wh_"))
        registry_database = stack.enter_context(isolated_database(registry_url, "verify_rg_"))
        warehouse = create_warehouse(WarehouseSettings(database_url=warehouse_database,
            admin_token=admin, service_token=service, artifact_path=directory / "artifacts",
            max_attempts=1, langsmith_api_key=key, langsmith_endpoint=endpoint,
            langsmith_workspace_id=workspace_id, langsmith_export=False), initialize_schema=True)
        registry = create_registry(RegistrySettings(database_url=registry_database,
            admin_token=registry_admin, warehouse_token=service, warehouse_url="http://warehouse",
            poll_interval_seconds=0.05), warehouse_transport=httpx.ASGITransport(app=warehouse),
            initialize_schema=True)
        async with warehouse.router.lifespan_context(warehouse), registry.router.lifespan_context(registry):
            async with (
                httpx.AsyncClient(transport=httpx.ASGITransport(app=warehouse), base_url="http://warehouse") as wh,
                httpx.AsyncClient(transport=httpx.ASGITransport(app=registry), base_url="http://registry") as rg,
            ):
                issued = await wh.post("/v1/applications", json={"application_id": application},
                    headers={"Authorization": "Bearer " + admin})
                issued.raise_for_status()
                application_headers = {"Authorization": "Bearer " + issued.json()["token"]}
                packets = []
                for batch_index, batch in enumerate(batches):
                    for kind in ("text", "json"):
                        style = "concise" if batch_index == 0 else "careful"
                        instruction = (f"Be {style}. Reply exactly: Hello Foundry." if kind == "text"
                                       else f'Be {style}. Return only valid JSON: {{"ok":true}}.')
                        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                  "required": ["ok"], "additionalProperties": False} if kind == "json" else None
                        packet = Packet(application_id=application, batch_id=batch,
                            idempotency_key=f"{batch}:{kind}",
                            experiment=ExperimentMetadata(dataset_id=dataset, case_id=kind,
                                label="Synthetic " + batch, tags=["packet-foundry-verification"]),
                            request=GenerationRequest(model=args.model, instructions=instruction,
                                parameters={"num_predict": 64, "num_ctx": 2048, "temperature": 0, "seed": 7}),
                            materials=[Material(name="source", text="Synthetic verification material: a small robot greets a visitor.")],
                            output=OutputSpec(kind=kind, json_schema=schema))
                        result = await wh.post("/v1/packets", json=packet.model_dump(mode="json"),
                                               headers=application_headers)
                        result.raise_for_status()
                        packets.append(packet)
                    sealed = await wh.post(f"/v1/batches/{batch}/seal", headers=application_headers)
                    sealed.raise_for_status()
                daemons, tasks = [], []
                try:
                    for kind in ("text", "json"):
                        factory_id = "factory-" + kind + "-" + suffix
                        enrolled = await rg.post("/v1/enrollments", json={"factory_id": factory_id, "name": "Synthetic " + kind},
                            headers={"Authorization": "Bearer " + registry_admin})
                        enrolled.raise_for_status()
                        credential_file = directory / (factory_id + ".token")
                        credential_file.write_text(enrolled.json()["token"], encoding="utf-8")
                        if os.name != "nt":
                            credential_file.chmod(0o600)
                        config = FactoryConfig(registry_url="http://registry", registry_token_env=None,
                            registry_token_file=credential_file,
                            registration=FactoryRegistration(factory_id=factory_id, name="Synthetic " + kind,
                                profiles=[ExecutionProfile(profile_id=kind, model=args.model, backend="ollama",
                                                           output_kinds=[kind])]),
                            backends={"ollama": BackendConfig(kind="ollama", auth_mode="none",
                                base_url=ollama_url, timeout_seconds=args.generation_timeout)},
                            data_dir=directory / factory_id, reconnect_seconds=0.2,
                            tracing=TracingConfig(enabled=True, project=trace_project_name,
                                endpoint=endpoint, capture_content=True))
                        connection = httpx.AsyncClient(transport=httpx.ASGITransport(app=registry),
                            base_url="http://registry/", headers={"Authorization": "Bearer " + config.token()})
                        daemon = FactoryDaemon(config, registry=RegistryClient(config, connection))
                        daemons.append(daemon)
                        tasks.append(asyncio.create_task(daemon.run()))
                    progress("generating", expected=4, factories=2)
                    last_terminal = -1
                    async with asyncio.timeout(args.generation_timeout + 60):
                        while True:
                            for task in tasks:
                                if task.done():
                                    task.result()
                                    raise RuntimeError("factory stopped before verification completed")
                            total = 0
                            for batch in batches:
                                response = await wh.get(f"/v1/batches/{batch}", headers=application_headers)
                                response.raise_for_status()
                                total += response.json()["terminal"]
                            if total != last_terminal:
                                progress("generating", completed=total, expected=4)
                                last_terminal = total
                            if total == 4:
                                break
                            await asyncio.sleep(0.3)
                    for batch in batches:
                        response = await wh.get("/v1/results", params={"batch_id": batch, "limit": 10},
                                                headers=application_headers)
                        response.raise_for_status()
                        results = [ResultEnvelope.model_validate(item) for item in response.json()]
                        if len(results) != 2 or any(result.status != "succeeded" for result in results):
                            raise RuntimeError("local generation did not produce four successful outcomes")
                        for result in results:
                            trace_id = result.result.metadata.get("langsmith_trace_id")
                            if not isinstance(trace_id, str):
                                raise RuntimeError("factory tracing was not active")
                            trace_ids.add(trace_id)
                finally:
                    for daemon in daemons:
                        daemon.stop.set()
                    if tasks:
                        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 30)
                if len(trace_ids) != 4:
                    raise RuntimeError("expected four distinct trace IDs")
                progress("exporting", batches=2)
                repository = warehouse.state.repository
                deadline = time.monotonic() + args.consistency_timeout
                actual_dataset_id = None
                summary = None
                while time.monotonic() < deadline:
                    await export_completed(repository)
                    with repository.sessions() as session:
                        exported = [session.get(BatchRow, (application, batch)) for batch in batches]
                        if all(batch.export_state == "exported" for batch in exported):
                            dataset_ids = {str(batch.export_response["dataset"]["id"]) for batch in exported
                                           if batch.export_response.get("dataset")}
                            # Reconciled responses may omit dataset; read its native project reference.
                            if len(dataset_ids) == 1:
                                actual_dataset_id = next(iter(dataset_ids))
                            elif not dataset_ids:
                                try:
                                    project = await asyncio.to_thread(client.read_project,
                                        project_name=expected_experiment_names[0])
                                    if project.reference_dataset_id:
                                        actual_dataset_id = str(project.reference_dataset_id)
                                except Exception:
                                    pass
                    if actual_dataset_id:
                        try:
                            summary = await asyncio.wait_for(asyncio.to_thread(live_snapshot, client,
                                trace_project_name, trace_ids, factory_ids, expected_experiment_names,
                                actual_dataset_id), timeout=max(0.1, min(30, deadline - time.monotonic())))
                        except Exception:
                            summary = None
                        if summary:
                            progress("verified", factory_runs=4, experiment_projects=2, comparison_cases=2)
                            if args.verify_attachments:
                                progress("verifying-synthetic-attachments")
                                try:
                                    summary["attachment_transport"] = await verify_attachment_transport(args,
                                        endpoint, key, workspace_id, client, directory / "attachment-check",
                                        trace_project_name + "-attachments")
                                    summary["attachment_transport"]["status"] = "passed"
                                except Exception as exc:
                                    summary["status"] = "partial"
                                    summary["attachment_transport"] = {"status": "failed",
                                        "error_code": type(exc).__name__, "synthetic_transport_only": True,
                                        "real_media_generation": False}
                            return summary
                    await asyncio.sleep(min(3, max(0, deadline - time.monotonic())))
                raise TimeoutError("live LangSmith consistency verification did not complete within its bound")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Private dotenv file; values are never printed")
    parser.add_argument("--output", type=Path, default=Path(".local/live-evidence/langsmith.json"))
    parser.add_argument("--model", default="qwen2.5-coder:0.5b", help="Already-installed local Ollama model")
    parser.add_argument("--verify-attachments", action="store_true",
                        help="Also upload/fetch synthetic PNG fixtures in a separate trace project")
    parser.add_argument("--generation-timeout", type=float, default=180)
    parser.add_argument("--consistency-timeout", type=float, default=180)
    args = parser.parse_args()
    if args.generation_timeout <= 0 or args.consistency_timeout <= 0:
        parser.error("timeouts must be positive")
    # Third-party diagnostics can include private endpoint details. Emit only the
    # script's synthetic progress/counts and sanitized failure categories.
    logging.disable(logging.CRITICAL)
    try:
        summary = asyncio.run(verify(args))
    except Exception as exc:
        summary = {"status": "failed", "error_code": type(exc).__name__,
                   **VERIFICATION_STATE}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary), flush=True)
    raise SystemExit(0 if summary["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
