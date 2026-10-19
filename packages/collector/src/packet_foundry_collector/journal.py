"""Durable local delivery state. No provider or application credentials are stored."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from packet_foundry_contracts import Packet, PacketRecord, ResultEnvelope, canonical_json


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS submissions (
                source_key TEXT PRIMARY KEY,
                submission_hash TEXT NOT NULL,
                packet_json TEXT NOT NULL,
                remote_packet_id TEXT,
                sent INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                packet_id TEXT PRIMARY KEY,
                envelope_json TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS schedules (
                name TEXT PRIMARY KEY,
                definition_hash TEXT NOT NULL,
                next_run TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def submission(self, source_key: str) -> Packet | None:
        row = self.db.execute(
            "SELECT packet_json FROM submissions WHERE source_key=?", (source_key,)
        ).fetchone()
        return Packet.model_validate_json(row[0]) if row else None

    def stage(self, source_key: str, packet: Packet) -> Packet:
        """Persist before the network request and preserve the very first manifest."""
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO submissions(source_key,submission_hash,packet_json) "
                "VALUES(?,?,?)",
                (source_key, packet.submission_hash(), packet.model_dump_json()),
            )
            row = self.db.execute(
                "SELECT * FROM submissions WHERE source_key=?", (source_key,)
            ).fetchone()
            if row["submission_hash"] != packet.submission_hash():
                raise ValueError("source/idempotency key already holds a different submission")
            return Packet.model_validate_json(row["packet_json"])

    def pending_submissions(self) -> list[tuple[str, Packet]]:
        rows = self.db.execute(
            "SELECT source_key,packet_json FROM submissions WHERE sent=0 ORDER BY rowid"
        ).fetchall()
        return [(row[0], Packet.model_validate_json(row[1])) for row in rows]

    def sent(self, source_key: str, record: PacketRecord) -> None:
        with self.db:
            self.db.execute(
                "UPDATE submissions SET sent=1,remote_packet_id=? WHERE source_key=?",
                (record.packet.packet_id, source_key),
            )

    def receive(self, envelope: ResultEnvelope) -> None:
        serialized = canonical_json(envelope.model_dump(mode="json"))
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO deliveries(packet_id,envelope_json) VALUES(?,?)",
                (envelope.packet.packet_id, serialized),
            )
            row = self.db.execute(
                "SELECT envelope_json FROM deliveries WHERE packet_id=?", (envelope.packet.packet_id,)
            ).fetchone()
            if row[0] != serialized:
                raise ValueError("warehouse changed an already received outcome")

    def pending_deliveries(self) -> list[tuple[ResultEnvelope, bool]]:
        rows = self.db.execute(
            "SELECT envelope_json,consumed FROM deliveries WHERE acknowledged=0 ORDER BY rowid"
        ).fetchall()
        return [(ResultEnvelope.model_validate_json(row[0]), bool(row[1])) for row in rows]

    def consumed(self, packet_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE deliveries SET consumed=1 WHERE packet_id=?", (packet_id,))

    def acknowledged(self, packet_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE deliveries SET acknowledged=1 WHERE packet_id=? AND consumed=1", (packet_id,)
            )

    def due(self, name: str, definition_hash: str, now: datetime) -> datetime:
        with self.db:
            row = self.db.execute("SELECT * FROM schedules WHERE name=?", (name,)).fetchone()
            if row is None or row["definition_hash"] != definition_hash:
                self.db.execute(
                    "INSERT INTO schedules(name,definition_hash,next_run) VALUES(?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET definition_hash=excluded.definition_hash, "
                    "next_run=excluded.next_run",
                    (name, definition_hash, now.isoformat()),
                )
                return now
            return datetime.fromisoformat(row["next_run"])

    def schedule_completed(self, name: str, next_run: datetime) -> None:
        with self.db:
            self.db.execute(
                "UPDATE schedules SET next_run=? WHERE name=?", (next_run.isoformat(), name)
            )

    def status(self) -> dict[str, int]:
        return {
            "pending_submissions": self.db.execute(
                "SELECT count(*) FROM submissions WHERE sent=0"
            ).fetchone()[0],
            "sent_submissions": self.db.execute(
                "SELECT count(*) FROM submissions WHERE sent=1"
            ).fetchone()[0],
            "pending_consumption": self.db.execute(
                "SELECT count(*) FROM deliveries WHERE consumed=0"
            ).fetchone()[0],
            "pending_acknowledgements": self.db.execute(
                "SELECT count(*) FROM deliveries WHERE consumed=1 AND acknowledged=0"
            ).fetchone()[0],
            "acknowledged": self.db.execute(
                "SELECT count(*) FROM deliveries WHERE acknowledged=1"
            ).fetchone()[0],
        }
