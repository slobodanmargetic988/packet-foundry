"""Independent registration, heartbeat, work and renewal loops with crash recovery."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from packet_foundry_contracts import (
    Assignment,
    Completion,
    ErrorInfo,
    Failure,
    ResultPackage,
    utcnow,
)

from .config import FactoryConfig
from .journal import Journal
from .protocol import RegistryClient
from .providers import Provider, ProviderError
from .tracing import Tracer

log = logging.getLogger(__name__)


def stale_error(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {403, 404, 409, 410}


def retryable_registry_error(exc: Exception) -> bool:
    return (isinstance(exc, httpx.TransportError)
            or isinstance(exc, httpx.HTTPStatusError)
            and (exc.response.status_code in {408, 429} or exc.response.status_code >= 500))


class FactoryDaemon:
    def __init__(self, config: FactoryConfig, *, registry: RegistryClient | None = None,
                 provider_factory=Provider):
        self.config = config
        self.journal = Journal(config.data_dir)
        self.registry = registry or RegistryClient(config)
        self.provider_factory = provider_factory
        self.tracer = Tracer(config.tracing)
        self.stop = asyncio.Event()
        self.tasks: dict[str, tuple[str, asyncio.Task]] = {}
        self.groups = {key: asyncio.Semaphore(size)
                       for key, size in config.registration.resource_groups.items()}

    async def pause(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), seconds)

    async def register(self) -> None:
        while not self.stop.is_set():
            try:
                await self.registry.register()
                return
            except (httpx.HTTPError, OSError):
                log.warning("Registry registration unavailable; reconnecting")
                await self.pause(self.config.reconnect_seconds)

    async def heartbeats(self) -> None:
        while not self.stop.is_set():
            try:
                await self.registry.heartbeat()
            except (httpx.HTTPError, OSError):
                log.warning("Registry heartbeat unavailable")
                await self.register()
            await self.pause(self.config.heartbeat_seconds)

    def capacity(self) -> bool:
        counts = {group: 0 for group in self.groups}
        for group, _ in self.tasks.values():
            counts[group] += 1
        return any(counts[group] < size
                   for group, size in self.config.registration.resource_groups.items())

    def launch(self, assignment: Assignment, *, recovery: bool = False) -> None:
        if assignment.attempt_id in self.tasks:
            return
        self.journal.receive(assignment)
        task = asyncio.create_task(self.execute(assignment, recovery=recovery))
        local_profile = next((p for p in self.config.registration.profiles
                              if p.profile_id == assignment.profile.profile_id), None)
        group = local_profile.resource_group if local_profile else next(iter(self.groups))
        self.tasks[assignment.attempt_id] = (group, task)

    async def work(self) -> None:
        while not self.stop.is_set():
            for attempt_id, (_, task) in list(self.tasks.items()):
                if task.done():
                    self.tasks.pop(attempt_id)
                    try:
                        task.result()
                    except Exception:
                        log.error("Attempt stopped unexpectedly; local journal retained for recovery")
            if not self.capacity():
                await self.pause(0.1)
                continue
            try:
                assignment = await self.registry.work()
                if assignment:
                    self.launch(assignment)
            except (httpx.HTTPError, OSError):
                await self.pause(self.config.reconnect_seconds)
                await self.register()

    async def run(self) -> None:
        await self.register()
        if self.stop.is_set():
            return
        # Recovery first: completion replays never repeat generation.
        for row in self.journal.pending():
            assignment = Assignment.model_validate_json(row["assignment"])
            if assignment.profile.resource_group not in self.groups:
                self.journal.update(assignment.attempt_id, state="stale")
                continue
            self.launch(assignment, recovery=True)
        heartbeat = asyncio.create_task(self.heartbeats())
        work = asyncio.create_task(self.work())
        stopped = asyncio.create_task(self.stop.wait())
        try:
            done, _ = await asyncio.wait([heartbeat, work, stopped], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            heartbeat.cancel()
            work.cancel()
            stopped.cancel()
            for _, task in self.tasks.values():
                task.cancel()
            await asyncio.gather(heartbeat, work, stopped, *(t for _, t in self.tasks.values()),
                                 return_exceptions=True)
            await self.registry.close()
            await self.tracer.close()
            self.journal.close()

    async def renewal(self, assignment: Assignment, lost: asyncio.Event) -> None:
        expires = assignment.lease_expires_at
        while not self.stop.is_set() and not lost.is_set():
            remaining = (expires - utcnow()).total_seconds()
            if remaining <= 0:
                lost.set()
                return
            await asyncio.sleep(min(self.config.renew_seconds, max(0.01, remaining / 3)))
            try:
                raw = await self.registry.renew(assignment)
                expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception as exc:
                if stale_error(exc):
                    lost.set()
                    return
                log.warning("Lease renewal unavailable; retrying before lease expiry")

    async def execute(self, assignment: Assignment, *, recovery: bool) -> None:
        row = self.journal.get(assignment.attempt_id)
        # Replay the exact completion before asking if the lease is current. It may already
        # have been accepted immediately before a connection loss or factory crash.
        if row["completion"]:
            await self.deliver(assignment, completion=Completion.model_validate_json(row["completion"]))
            return
        if row["failure"]:
            await self.deliver(assignment, failure=Failure.model_validate_json(row["failure"]))
            return
        lost = asyncio.Event()
        renewer = None
        try:
            while True:
                try:
                    assignment = await self.registry.current(assignment)
                    break
                except Exception as exc:
                    if stale_error(exc):
                        self.journal.update(assignment.attempt_id, state="stale")
                        return
                    await self.pause(self.config.reconnect_seconds)
                    if self.stop.is_set():
                        return
            renewer = asyncio.create_task(self.renewal(assignment, lost))
            invocation = asyncio.create_task(self.process(assignment, recovery=recovery))
            lease_lost = asyncio.create_task(lost.wait())
            try:
                done, _ = await asyncio.wait([invocation, lease_lost], return_when=asyncio.FIRST_COMPLETED)
                if invocation in done:
                    await invocation
                else:
                    invocation.cancel()
                    await asyncio.gather(invocation, return_exceptions=True)
                    await self.tracer.fail(assignment, "lease_lost")
                    self.journal.update(assignment.attempt_id, state="stale")
                    log.warning("Attempt lease was lost; local output retained, execution stopped")
            finally:
                invocation.cancel()
                lease_lost.cancel()
                await asyncio.gather(invocation, lease_lost, return_exceptions=True)
        except asyncio.CancelledError:
            await self.tracer.fail(assignment, "factory_stopped")
            raise
        except Exception as exc:
            if stale_error(exc):
                self.journal.update(assignment.attempt_id, state="stale")
            else:
                # No exception strings or source content enter public logs/results.
                error = exc if isinstance(exc, ProviderError) else ProviderError(
                    "factory_execution", "Factory could not complete this attempt", False)
                await self.tracer.fail(assignment, error.code)
                failure = Failure(factory_id=assignment.factory_id,
                                  fencing_token=assignment.fencing_token,
                                  error=ErrorInfo(code=error.code, message=error.message,
                                                  retryable=error.retryable))
                self.journal.update(assignment.attempt_id, state="failure_pending",
                                    failure=failure.model_dump(mode="json"))
                await self.deliver(assignment, failure=failure)
        finally:
            if renewer:
                renewer.cancel()
                await asyncio.gather(renewer, return_exceptions=True)

    async def process(self, assignment: Assignment, *, recovery: bool) -> None:
        row = self.journal.get(assignment.attempt_id)
        profile = next((p for p in self.config.registration.profiles
                        if p.profile_id == assignment.profile.profile_id), None)
        if (assignment.factory_id != self.config.registration.factory_id or profile is None
                or profile != assignment.profile or not profile.matches(assignment.packet)):
            raise ProviderError("invalid_assignment", "Assignment does not match a configured profile")
        backend = self.config.backends[profile.backend]
        output = json.loads(row["output"]) if row["output"] else None
        operation = json.loads(row["operation"]) if row["operation"] else None
        if not output:
            if recovery and row["state"] == "running" and not operation:
                # Crash between submission and receipt cannot safely repeat a synchronous
                # call (or an async submission with no recorded operation ID).
                raise ProviderError("execution_uncertain", "Factory restarted before recording the provider result; manual retry required")
            directory = self.journal.attempt_dir(assignment.attempt_id)
            inputs: dict[str, Path] = {}
            seen = set()
            for index, material in enumerate(assignment.packet.materials):
                if material.name in seen:
                    raise ProviderError("invalid_input", "Material names must be unique")
                seen.add(material.name)
                if material.artifact:
                    path = directory / f"input-{index}"
                    while True:
                        try:
                            await self.registry.download(assignment, material.artifact, path)
                            break
                        except httpx.HTTPError as exc:
                            if stale_error(exc):
                                raise
                            if not retryable_registry_error(exc):
                                raise ProviderError("registry_artifact", "Registry rejected an input artifact") from exc
                            await self.pause(self.config.reconnect_seconds)
                    inputs[material.name] = path

            async def save_operation(value: dict) -> None:
                value["_deadline"] = (utcnow() + timedelta(seconds=backend.timeout_seconds)).isoformat()
                self.journal.update(assignment.attempt_id, state="provider_pending", operation=value)

            provider = self.provider_factory(backend, self.config.max_artifact_bytes)
            async with self.groups[profile.resource_group]:
                self.journal.update(assignment.attempt_id, state="running")
                try:
                    trace_id = await self.tracer.start(assignment, inputs)
                    while True:
                        try:
                            output = await provider.generate(assignment, directory, inputs, save_operation, operation)
                            break
                        except ProviderError as exc:
                            recorded = self.journal.get(assignment.attempt_id)["operation"]
                            if not recorded or not exc.retryable:
                                raise
                            operation = json.loads(recorded)
                            await self.pause(self.config.reconnect_seconds)
                    if trace_id:
                        output["result"].setdefault("metadata", {})["langsmith_trace_id"] = trace_id
                    self.journal.update(assignment.attempt_id, state="generated", output=output)
                    await self.tracer.finish(assignment, output)
                finally:
                    await provider.client.aclose()
        await self.upload_output(assignment, output)

    async def upload_output(self, assignment: Assignment, output: dict) -> None:
        for artifact in output["files"]:
            if not artifact["ref"]:
                while True:
                    try:
                        reference = await self.registry.upload(assignment, Path(artifact["path"]), artifact["mime_type"])
                        break
                    except httpx.HTTPError as exc:
                        if stale_error(exc):
                            raise
                        if not retryable_registry_error(exc):
                            raise ProviderError("registry_artifact", "Registry rejected an output artifact") from exc
                        await self.pause(self.config.reconnect_seconds)
                artifact["ref"] = reference.model_dump(mode="json")
                self.journal.update(assignment.attempt_id, state="generated", output=output)
        result = ResultPackage.model_validate(output["result"])
        from packet_foundry_contracts import ArtifactRef
        result.artifacts = [ArtifactRef.model_validate(artifact["ref"]) for artifact in output["files"]]
        # Revalidate after construction because assignment validation is not enabled on wire models.
        result = ResultPackage.model_validate(result.model_dump(mode="json"))
        completion = Completion(factory_id=assignment.factory_id, fencing_token=assignment.fencing_token,
                                result=result)
        self.journal.update(assignment.attempt_id, state="completion_pending",
                            completion=completion.model_dump(mode="json"))
        await self.deliver(assignment, completion=completion)

    async def deliver(self, assignment: Assignment, *, completion: Completion | None = None,
                      failure: Failure | None = None) -> None:
        while not self.stop.is_set():
            try:
                if completion:
                    await self.registry.complete(assignment, completion)
                elif failure:
                    await self.registry.fail(assignment, failure)
                self.journal.update(assignment.attempt_id, state="acknowledged")
                return
            except Exception as exc:
                if stale_error(exc):
                    self.journal.update(assignment.attempt_id, state="stale")
                    return
                if not retryable_registry_error(exc):
                    # Preserve the exact rejected manifest for operator inspection; do not
                    # keep a lease alive forever for a permanent protocol/validation error.
                    if completion:
                        rejected = Failure(factory_id=assignment.factory_id,
                            fencing_token=assignment.fencing_token,
                            error=ErrorInfo(code="registry_rejected_output",
                                message="Registry rejected the generated output", retryable=False))
                        self.journal.update(assignment.attempt_id, state="failure_pending", completion=None,
                                            failure=rejected.model_dump(mode="json"))
                        await self.deliver(assignment, failure=rejected)
                        return
                    self.journal.update(assignment.attempt_id, state="stale")
                    log.error("Registry permanently rejected an attempt outcome; local spool retained")
                    return
                await self.pause(self.config.reconnect_seconds)


class InstanceLock:
    """Keep two daemons from sharing one recovery journal on any supported OS."""

    def __init__(self, directory: Path):
        directory.expanduser().mkdir(parents=True, exist_ok=True)
        self.file = (directory.expanduser() / "factory.lock").open("a+b")

    def __enter__(self):
        if __import__("os").name == "nt":
            import msvcrt
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *_):
        self.file.close()
