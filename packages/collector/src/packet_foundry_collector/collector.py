"""Durable orchestration, independent of any particular application's adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from packet_foundry_contracts import Packet, PacketRecord

from .adapters import CollectorAdapter
from .client import WarehouseClient
from .journal import Journal

T = TypeVar("T")


class Collector:
    def __init__(
        self, client: WarehouseClient, journal: Journal, adapter: CollectorAdapter
    ) -> None:
        self.client = client
        self.journal = journal
        self.adapter = adapter

    async def prepare(self) -> int:
        """Discover and durably stage manifests before any submission attempts."""
        count = 0
        for source in await self.adapter.sources():
            if self.journal.submission(source.key) is None:
                packet = await self.adapter.prepare(source, self.client)
                self.journal.stage(source.key, packet)
                count += 1
        return count

    async def submit(self, packet: Packet) -> PacketRecord:
        """Immediately submit one request, preserving its payload across retry."""
        key = f"packet:{packet.application_id}:{packet.idempotency_key}"
        staged = self.journal.stage(key, packet)
        record = await self.client.submit(staged)
        self._validate_submission(staged, record)
        self.journal.sent(key, record)
        return record

    async def send(self) -> int:
        await self.prepare()
        count = 0
        for source_key, packet in self.journal.pending_submissions():
            record = await self.client.submit(packet)
            self._validate_submission(packet, record)
            self.journal.sent(source_key, record)
            count += 1
        return count

    @staticmethod
    def _validate_submission(packet: Packet, record: PacketRecord) -> None:
        if record.packet.submission_hash() != packet.submission_hash():
            raise ValueError("warehouse returned a different submission")

    async def consume(self) -> int:
        count = 0
        for envelope, consumed in self.journal.pending_deliveries():
            packet_id = envelope.packet.packet_id
            if not consumed:
                await self.adapter.consume(envelope, self.client)
                self.journal.consumed(packet_id)
            await self.client.acknowledge(packet_id)
            self.journal.acknowledged(packet_id)
            count += 1
        return count

    async def collect(self, *, limit: int = 100, batch_id: str | None = None) -> int:
        # Retry journaled commits/ACKs even if the server stopped returning them.
        consumed = await self.consume()
        for envelope in await self.client.results(limit=limit, batch_id=batch_id):
            self.journal.receive(envelope)
        return consumed + await self.consume()

    async def seal_batch(self, batch_id: str) -> None:
        """Call only after every source in this batch has been successfully submitted."""
        await self.client.seal_batch(batch_id)

    async def wait(
        self, packet_id: str, *, timeout: float = 300, poll_interval: float = 1
    ) -> PacketRecord:
        """Wait for a terminal outcome without ACKing or removing it from delivery."""
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("timeout and poll_interval must be positive")

        async def poll() -> PacketRecord:
            while True:
                record = await self.client.status(packet_id)
                if record.status in {"succeeded", "failed", "cancelled"}:
                    return record
                await asyncio.sleep(poll_interval)

        return await self._timeout(poll(), timeout)

    @staticmethod
    async def _timeout(operation: Awaitable[T], timeout: float) -> T:
        async with asyncio.timeout(timeout):
            return await operation
