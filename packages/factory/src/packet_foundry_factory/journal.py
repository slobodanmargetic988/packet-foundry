"""A local recovery journal, never the authority for a job's ownership."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from packet_foundry_contracts import Assignment, utcnow


class Journal:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.directory / "journal.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY, assignment TEXT NOT NULL, state TEXT NOT NULL,
            operation TEXT, output TEXT, completion TEXT, failure TEXT, updated_at TEXT NOT NULL
        )""")
        self.db.commit()
        if os.name != "nt":
            (self.directory / "journal.sqlite3").chmod(0o600)

    def receive(self, assignment: Assignment) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO attempts(attempt_id,assignment,state,updated_at) VALUES(?,?,?,?)",
            (assignment.attempt_id, assignment.model_dump_json(), "received", utcnow().isoformat()),
        )
        self.db.commit()

    def get(self, attempt_id: str) -> dict:
        row = self.db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        return dict(row)

    def update(self, attempt_id: str, *, state: str | None = None, **values) -> None:
        if set(values) - {"operation", "output", "completion", "failure"}:
            raise ValueError("invalid journal column")
        fields = {key: json.dumps(value, ensure_ascii=False) if value is not None else None
                  for key, value in values.items()}
        if state:
            fields["state"] = state
        fields["updated_at"] = utcnow().isoformat()
        names = list(fields)
        self.db.execute(
            "UPDATE attempts SET " + ",".join(f"{key}=?" for key in names) + " WHERE attempt_id=?",
            [fields[key] for key in names] + [attempt_id],
        )
        self.db.commit()

    def pending(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM attempts WHERE state NOT IN ('acknowledged','stale') ORDER BY updated_at"
        )]

    def counts(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT state,count(*) FROM attempts GROUP BY state"))

    def attempt_dir(self, attempt_id: str) -> Path:
        # Wire identifiers contain ':' and are not portable Windows path components.
        target = self.directory / "spool" / hashlib.sha256(attempt_id.encode()).hexdigest()
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        return target

    def close(self) -> None:
        self.db.close()
