"""Opt-in, restart-safe provisioning of registry-owned Runpod factory pods.

Cloud inventory is matched only to persisted unpredictable ownership names. Unknown
create outcomes are reconciled, never blindly retried into duplicate GPU pods.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import timedelta
from uuid import uuid4

import httpx
from packet_foundry_contracts import EnrollmentCreate, FactoryRegistration, Packet, utcnow
from sqlalchemy import select
from sqlalchemy.engine import Engine

from .database import CloudPod, Factory, RegistryIdentity, advisory_lock, utc
from .security import enroll
from .settings import RegistrySettings, RunpodTemplate

logger = logging.getLogger(__name__)
TERMINAL_STATES = {"terminated", "missing", "abandoned"}


class RunpodError(Exception):
    """Safe error code; never carries provider payloads, environment, or credentials."""


class RunpodController:
    def __init__(self, settings: RegistrySettings, engine: Engine, session_factory,
                 warehouse: httpx.AsyncClient, *, transport=None):
        self.settings = settings
        self.engine = engine
        self.sessions = session_factory
        self.warehouse = warehouse
        self.transport = transport
        self.client: httpx.AsyncClient | None = None

    async def open(self):
        key = self.settings.runpod_api_key.get_secret_value()
        if not key:
            raise RuntimeError("Runpod is enabled but its environment credential is missing")
        self.client = httpx.AsyncClient(
            base_url=self.settings.runpod_api_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + key}, timeout=httpx.Timeout(30, connect=5),
            transport=self.transport, follow_redirects=False,
        )
        with advisory_lock(self.engine, "runpod:identity") as acquired:
            if acquired:
                with self.sessions.begin() as session:
                    if session.get(RegistryIdentity, 1) is None:
                        session.add(RegistryIdentity(singleton=1, owner_id=str(uuid4())))

    async def close(self):
        if self.client:
            await self.client.aclose()

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Exception text may contain credentials/URLs: log only its class.
                logger.warning("Runpod reconciliation deferred (%s)", type(error).__name__)
            await asyncio.sleep(self.settings.runpod_poll_seconds)

    async def provider(self, method: str, path: str, *, body=None):
        response = await self.client.request(method, path, json=body)
        if not response.is_success:
            raise RunpodError("provider_http_" + str(response.status_code))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise RunpodError("provider_invalid_json") from None

    async def demand(self) -> list[Packet]:
        response = await self.warehouse.get("v1/internal/demand")
        if not response.is_success:
            raise RunpodError("warehouse_demand_unavailable")
        return [Packet.model_validate(value) for value in response.json()]

    async def activity(self, factory_id: str) -> int:
        response = await self.warehouse.get(f"v1/internal/factories/{factory_id}/activity")
        if not response.is_success:
            raise RunpodError("warehouse_activity_unavailable")
        value = response.json().get("active_attempts")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RunpodError("warehouse_activity_invalid")
        return value

    def error(self, record_id: str, code: str, *, state: str | None = None):
        with self.sessions.begin() as session:
            row = session.get(CloudPod, record_id)
            row.error_code = code
            if state and row.state != state:
                row.state = state
                row.updated_at = utcnow()

    def change(self, record_id: str, state: str, *, busy: bool = False):
        with self.sessions.begin() as session:
            row = session.get(CloudPod, record_id)
            row.state = state
            row.updated_at = utcnow()
            row.error_code = None
            if busy:
                row.last_busy_at = utcnow()

    async def tick(self):
        if not self.settings.runpod_enabled:
            return
        # A session-level PG lock is held over provider I/O so replicas cannot
        # concurrently create or stop cloud resources. Work claims use other locks.
        with advisory_lock(self.engine, "runpod:leader") as acquired:
            if not acquired:
                return
            inventory = await self.provider("GET", "pods")
            if not isinstance(inventory, list) or any(not isinstance(pod, dict) for pod in inventory):
                raise RunpodError("provider_inventory_invalid")
            queued = await self.demand()
            templates = {t.template_id: t for t in self.settings.runpod_templates}
            with self.sessions() as session:
                rows = session.scalars(select(CloudPod).where(
                    CloudPod.state.not_in({"terminated", "missing"}),
                )).all()
            for row in rows:
                template = templates.get(row.template_id)
                try:
                    await self.reconcile(row, template, inventory, queued)
                except httpx.HTTPError:
                    self.error(row.record_id, "provider_transport_error")
                except RunpodError as error:
                    self.error(row.record_id, str(error))
            for template in templates.values():
                if not template.enabled:
                    continue
                matching = [packet for packet in queued if packet.priority > 0 and any(
                    profile.matches(packet) for profile in template.profiles
                )]
                if not matching:
                    continue
                with self.sessions() as session:
                    current = session.scalars(select(CloudPod).where(
                        CloudPod.template_id == template.template_id,
                        CloudPod.state.not_in(TERMINAL_STATES),
                    )).all()
                # Stopped retained pods are resumed before new pods are created.
                stopped = [row for row in current if row.state == "stopped"]
                running = [row for row in current if row.state != "stopped"]
                capacity = sum(template.resource_groups.values())
                desired = min(template.max_factories, (len(matching) + capacity - 1) // capacity)
                for row in stopped[:max(0, desired - len(running))]:
                    await self.start(row)
                    running.append(row)
                for _ in range(max(0, desired - len(running))):
                    await self.create(template)

    async def create(self, template: RunpodTemplate):
        environment = dict(template.environment)
        for target, source in template.environment_from.items():
            if source not in os.environ:
                raise RunpodError("template_environment_missing")
            environment[target] = os.environ[source]
        record_id = str(uuid4())
        factory_id = "runpod-" + record_id
        with self.sessions() as session:
            owner = session.get(RegistryIdentity, 1)
            if owner is None:
                raise RunpodError("registry_identity_unavailable")
            name = "pf-" + owner.owner_id[:8] + "-" + record_id
            token = enroll(session, EnrollmentCreate(factory_id=factory_id, name=name))
            session.add(CloudPod(
                record_id=record_id, template_id=template.template_id, factory_id=factory_id,
                pod_name=name, state="creating", created_at=utcnow(), updated_at=utcnow(),
                shutdown_action=template.shutdown_action,
                last_busy_at=utcnow(),
            ))
            session.commit()
        config = dict(template.factory_config)
        config["registry_url"] = self.settings.public_url
        config["registry_token_env"] = "FOUNDRY_FACTORY_TOKEN"
        config["registration"] = FactoryRegistration(
            factory_id=factory_id, name=name, profiles=template.profiles,
            resource_groups=template.resource_groups,
        ).model_dump(mode="json")
        environment.update({
            "FOUNDRY_FACTORY_CONFIG_JSON": json.dumps(config, separators=(",", ":")),
            "FOUNDRY_FACTORY_REGISTRY_URL": self.settings.public_url,
            "FOUNDRY_FACTORY_ID": factory_id,
            "FOUNDRY_FACTORY_TOKEN": token,
        })
        body = dict(template.pod_parameters)
        body.update({"name": name, "env": environment})
        try:
            response = await self.provider("POST", "pods", body=body)
            provider_id = response.get("id") if isinstance(response, dict) else None
            if not isinstance(provider_id, str) or not provider_id:
                raise RunpodError("provider_create_response_invalid")
            with self.sessions.begin() as session:
                row = session.get(CloudPod, record_id)
                row.provider_pod_id = provider_id
                row.state = "starting"
                row.updated_at = utcnow()
        except (httpx.HTTPError, RunpodError):
            # Even an HTTP error can follow successful provider acceptance. The
            # next tick reconciles by the persisted ownership name. Never POST again.
            self.error(record_id, "create_outcome_unknown", state="create_unknown")

    async def start(self, pod: CloudPod):
        if not pod.provider_pod_id:
            raise RunpodError("owned_pod_id_missing")
        with self.sessions.begin() as session:
            factory = session.get(Factory, pod.factory_id)
            factory.draining = False
            # A resumed container must register/heartbeat anew, rather than inherit
            # readiness from a previously stopped process.
            factory.last_seen_at = None
        self.change(pod.record_id, "starting", busy=True)
        await self.provider("POST", f"pods/{pod.provider_pod_id}/start")

    async def recover(self, record_id: str, *, confirm_absent: bool = False):
        """Explicit operator recovery, serialized with automated reconciliation."""
        with advisory_lock(self.engine, "runpod:leader") as acquired:
            if not acquired:
                raise RunpodError("reconciliation_in_progress")
            with self.sessions() as session:
                row = session.get(CloudPod, record_id)
                if row is None:
                    raise RunpodError("owned_pod_not_found")
            inventory = await self.provider("GET", "pods")
            if not isinstance(inventory, list) or any(not isinstance(pod, dict) for pod in inventory):
                raise RunpodError("provider_inventory_invalid")
            matches = [pod for pod in inventory if pod.get("name") == row.pod_name]
            if row.state == "create_unknown" and not row.provider_pod_id:
                if matches or not confirm_absent:
                    raise RunpodError("review_provider_inventory_before_abandoning")
                with self.sessions.begin() as session:
                    session.get(CloudPod, row.record_id).state = "abandoned"
                    session.get(Factory, row.factory_id).revoked = True
                # Retain the record: if an old delayed create later appears,
                # reconciliation recognizes its ownership name and shuts it down.
                return {"state": "abandoned"}
            if row.state not in {"stopped", "startup_failed"} or len(matches) != 1:
                raise RunpodError("only_confirmed_stopped_owned_pods_can_resume")
            pod = matches[0]
            if pod.get("id") != row.provider_pod_id or pod.get("desiredStatus") not in {"EXITED", "STOPPED"}:
                raise RunpodError("provider_pod_not_confirmed_stopped")
            await self.start(row)
            return {"state": "starting"}

    async def reconcile(self, pod: CloudPod, template: RunpodTemplate | None,
                        inventory: list[dict], queued: list[Packet]):
        abandoned = pod.state == "abandoned"
        matches = [item for item in inventory if item.get("name") == pod.pod_name]
        if len(matches) > 1:
            self.error(pod.record_id, "duplicate_owned_name", state="create_unknown")
            return
        provider = next((item for item in inventory if pod.provider_pod_id
                         and item.get("id") == pod.provider_pod_id), None)
        if provider is None and matches:
            provider = matches[0]
            provider_id = provider.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                raise RunpodError("provider_inventory_invalid")
            with self.sessions.begin() as session:
                row = session.get(CloudPod, pod.record_id)
                row.provider_pod_id = provider_id
                row.state = "abandoned" if abandoned else "starting"
                row.error_code = None
            pod.provider_pod_id = provider_id
            pod.state = "abandoned" if abandoned else "starting"
        if provider is None:
            if pod.provider_pod_id:
                self.change(pod.record_id, "missing")
                with self.sessions.begin() as session:
                    session.get(Factory, pod.factory_id).revoked = True
            elif not abandoned and utcnow() - utc(pod.created_at) > timedelta(
                seconds=template.startup_timeout_seconds if template else 600,
            ):
                self.error(pod.record_id, "create_unknown_requires_inventory_review",
                           state="create_unknown")
            return
        # A persisted provider id is not enough to adopt/stop a renamed/reused pod.
        if provider.get("name") != pod.pod_name:
            self.error(pod.record_id, "ownership_name_mismatch", state="create_unknown")
            return
        status = str(provider.get("desiredStatus", "")).upper()
        if status in {"EXITED", "STOPPED"}:
            if pod.state == "startup_failed" or abandoned:
                return
            self.change(pod.record_id, "stopped")
            return
        if pod.state == "stopped":
            return
        with self.sessions() as session:
            factory = session.get(Factory, pod.factory_id)
            ready = bool(factory and not factory.revoked and factory.registration
                         and factory.last_seen_at and utc(factory.last_seen_at) >= utcnow() - timedelta(
                             seconds=self.settings.offline_after_seconds,
                         ))
        if pod.state in {"creating", "starting", "create_unknown"} and ready:
            self.change(pod.record_id, "running", busy=True)
            return
        timed_out = (pod.state in {"creating", "starting", "create_unknown"}
                     and utcnow() - utc(pod.updated_at) > timedelta(
                         seconds=template.startup_timeout_seconds if template else 600,
                     ))
        pending = template and template.enabled and any(packet.priority > 0 and any(
            profile.matches(packet) for profile in template.profiles
        ) for packet in queued)
        if pod.state == "running" and pending:
            with self.sessions.begin() as session:
                session.get(CloudPod, pod.record_id).last_busy_at = utcnow()
            return
        if pod.state in {"running", "draining", "stopping"} or timed_out or abandoned:
            with advisory_lock(self.engine, "factory:" + pod.factory_id) as acquired:
                if not acquired:
                    return
                active = await self.activity(pod.factory_id)
                if active:
                    with self.sessions.begin() as session:
                        session.get(CloudPod, pod.record_id).last_busy_at = utcnow()
                    return
                idle = utcnow() - utc(pod.last_busy_at) >= timedelta(
                    seconds=template.idle_timeout_seconds if template else 0,
                )
                if not (timed_out or abandoned or not template or not template.enabled or idle
                        or pod.state in {"draining", "stopping"}):
                    return
                with self.sessions.begin() as session:
                    session.get(Factory, pod.factory_id).draining = True
                    row = session.get(CloudPod, pod.record_id)
                    row.state = "draining"
                # The same factory advisory lock excludes every in-flight claim.
                # No new claim is possible after the persisted drain marker.
                method = "DELETE" if pod.shutdown_action == "terminate" else "POST"
                path = f"pods/{pod.provider_pod_id}"
                if method == "POST":
                    path += "/stop"
                self.change(pod.record_id, "stopping")
                await self.provider(method, path)
                self.change(pod.record_id, "terminated" if method == "DELETE" else "stopped")
                if abandoned and method != "DELETE":
                    self.change(pod.record_id, "abandoned")
                if timed_out and method != "DELETE":
                    self.error(pod.record_id, "startup_timeout", state="startup_failed")
                if method == "DELETE":
                    with self.sessions.begin() as session:
                        session.get(Factory, pod.factory_id).revoked = True
