"""SQLite state: which messages are handled, retry counts, prediction log, last run."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from triage.classifier import LayaResult
from triage.extract import Features
from triage.rules import RuleHits

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed (
    msg_id TEXT PRIMARY KEY,
    processed_at INTEGER NOT NULL,
    labels TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS predictions (
    msg_id TEXT, run_id TEXT, from_email TEXT, subject TEXT, type TEXT, type_conf REAL,
    top2 TEXT, needs_action REAL, urgency REAL, rule_hits TEXT, model TEXT, labels TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def is_done(self, msg_id: str) -> bool:
        row = self.db.execute("SELECT status FROM processed WHERE msg_id = ?", (msg_id,)).fetchone()
        return row is not None and row[0] in ("ok", "skipped", "failed")

    def attempts(self, msg_id: str) -> int:
        row = self.db.execute("SELECT attempts FROM processed WHERE msg_id = ?", (msg_id,)).fetchone()
        return row[0] if row else 0

    def retry_ids(self) -> list[str]:
        rows = self.db.execute("SELECT msg_id FROM processed WHERE status = 'error' ORDER BY msg_id")
        return [row[0] for row in rows]

    def mark(self, msg_id: str, status: str, labels: list[str]) -> None:
        bump = 1 if status == "error" else 0
        self.db.execute(
            "INSERT INTO processed (msg_id, processed_at, labels, status, attempts) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(msg_id) DO UPDATE SET processed_at = excluded.processed_at, "
            "labels = excluded.labels, status = excluded.status, "
            "attempts = processed.attempts + excluded.attempts",
            (msg_id, int(time.time()), json.dumps(labels), status, bump),
        )
        self.db.commit()

    def log_prediction(self, run_id: str, f: Features, laya: LayaResult, hits: RuleHits,
                       labels: list[str]) -> None:
        self.db.execute(
            "INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f.msg_id, run_id, f.from_email, f.subject, laya.type, laya.type_conf, json.dumps(laya.top2),
             laya.needs_action, laya.urgency, json.dumps(hits.names), laya.model, json.dumps(labels)),
        )
        self.db.commit()

    def get_last_run(self) -> int | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = 'last_run'").fetchone()
        return int(row[0]) if row else None

    def set_last_run(self, epoch: int) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES ('last_run', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(epoch),),
        )
        self.db.commit()
