"""Optional LangSmith tracing is fail-open and never blocks generation or lease renewal."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from uuid import uuid4

from packet_foundry_contracts import utcnow

from .config import TracingConfig

log = logging.getLogger(__name__)


class Tracer:
    def __init__(self, config: TracingConfig):
        self.config, self.client = config, None
        self.runs: dict[str, str] = {}
        self.starts: dict[str, asyncio.Task] = {}
        self.contexts: dict[str, dict] = {}
        self.tasks: set[asyncio.Task] = set()
        if config.enabled:
            try:
                from langsmith import Client
                key = os.environ.get(config.api_key_env)
                if not key:
                    raise ValueError("missing LangSmith credential")
                self.client = Client(api_key=key, api_url=config.endpoint, timeout_ms=5000)
            except (ImportError, ValueError):
                log.warning("LangSmith tracing configured but unavailable; install the tracing extra and configure its credential")

    def schedule(self, callback, **kwargs):
        async def send():
            try:
                await asyncio.to_thread(callback, **kwargs)
            except Exception:
                log.warning("LangSmith export unavailable; execution continues")
        task = asyncio.create_task(send())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def attachments(self, files: list[tuple[str, str, Path]]) -> dict:
        """Called in the exporter thread, after explicit content capture opt-in."""
        result = {}
        if not self.config.capture_content:
            return result
        remaining = self.config.max_attachment_bytes
        for name, mime, path in files:
            size = path.stat().st_size
            if size > remaining:
                log.warning("LangSmith attachment exceeds export size bound; file omitted from trace")
                continue
            content = path.read_bytes()
            if len(content) > remaining:
                continue
            remaining -= len(content)
            result[name] = (mime, content)
        return result

    async def start(self, assignment, input_files: dict[str, Path] | None = None):
        if self.client is None:
            return
        if len(self.tasks) >= 100:
            log.warning("LangSmith export backlog full; trace skipped, execution continues")
            return
        run_id = str(uuid4())
        self.runs[assignment.attempt_id] = run_id
        started = utcnow()
        context = {"trace_id": run_id,
                   "dotted_order": started.strftime("%Y%m%dT%H%M%S%fZ") + run_id}
        self.contexts[run_id] = context
        experiment = assignment.packet.experiment
        metadata = {"packet_id": assignment.packet.packet_id, "attempt_id": assignment.attempt_id,
                    "factory_id": assignment.factory_id, "profile_id": assignment.profile.profile_id,
                    "batch_id": assignment.packet.batch_id, "model": assignment.profile.model}
        if experiment:
            metadata.update(experiment.model_dump(mode="json"))
        inputs = {"instructions": assignment.resolved_instructions,
                  "system_prompt": assignment.resolved_system_prompt,
                  "materials": [m.model_dump(mode="json") for m in assignment.packet.materials]}
        def create():
            files = [(f"input_{index}_{material.kind}", material.artifact.mime_type,
                      input_files[material.name])
                     for index, material in enumerate(assignment.packet.materials)
                     if material.artifact and input_files and material.name in input_files]
            self.client.create_run(name="packet-generation", id=run_id, run_type="llm",
                project_name=self.config.project,
                start_time=started, **context,
                inputs=inputs if self.config.capture_content else {"packet_id": assignment.packet.packet_id},
                attachments=self.attachments(files),
                extra={"metadata": metadata}, tags=experiment.tags if experiment else [])
        self.starts[assignment.attempt_id] = self.schedule(create)
        return run_id

    async def finish(self, assignment, output):
        if self.client is None:
            return
        run_id = self.runs.pop(assignment.attempt_id, None)
        if run_id:
            start = self.starts.pop(assignment.attempt_id, None)
            context = self.contexts.pop(run_id, {})
            async def finish():
                if start:
                    await start
                try:
                    def update():
                        files = [(f"output_{index}", artifact["mime_type"], Path(artifact["path"]))
                                 for index, artifact in enumerate(output.get("files", []))]
                        self.client.update_run(run_id=run_id,
                            outputs=output["result"] if self.config.capture_content else {"completed": True},
                            attachments=self.attachments(files), end_time=utcnow(), **context)
                    await asyncio.to_thread(update)
                except Exception:
                    log.warning("LangSmith export unavailable; execution continues")
            task = asyncio.create_task(finish())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def fail(self, assignment, code: str):
        if self.client is None:
            return
        run_id = self.runs.pop(assignment.attempt_id, None)
        if run_id:
            start = self.starts.pop(assignment.attempt_id, None)
            context = self.contexts.pop(run_id, {})
            async def report():
                if start:
                    await start
                try:
                    await asyncio.to_thread(self.client.update_run, run_id=run_id, error=code,
                                            end_time=utcnow(), **context)
                except Exception:
                    log.warning("LangSmith export unavailable; execution continues")
            task = asyncio.create_task(report())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def close(self):
        if self.tasks:
            done, pending = await asyncio.wait(self.tasks, timeout=5)
            for task in pending:
                task.cancel()
        if self.client is not None and hasattr(self.client, "flush"):
            try:
                await asyncio.wait_for(asyncio.to_thread(self.client.flush, timeout=4), 5)
            except Exception:
                log.warning("LangSmith flush unavailable; execution continues")
