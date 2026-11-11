"""SQLite record of agent runs and their activity events (stored locally, never uploaded)."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..core import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, started REAL NOT NULL, goal TEXT NOT NULL, model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running', ended REAL, summary TEXT);
CREATE TABLE IF NOT EXISTS events (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE, seq INTEGER NOT NULL, ts REAL NOT NULL,
    kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, data TEXT NOT NULL,
    PRIMARY KEY (run_id, seq));
CREATE TABLE IF NOT EXISTS memory (
    server TEXT NOT NULL, topic TEXT NOT NULL, note TEXT NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY (server, topic));
CREATE TABLE IF NOT EXISTS pvp_state (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pvp_rounds (id INTEGER PRIMARY KEY, ts REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pvp_evaluations (id INTEGER PRIMARY KEY, ts REAL NOT NULL, generation INTEGER NOT NULL, share REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pvp_lessons (id INTEGER PRIMARY KEY, ts REAL NOT NULL, lesson TEXT NOT NULL);
"""
MAX_NOTES = 40


class ActivityStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            path = paths.ensure_private_dir(paths.data_dir()) / "activity.sqlite"
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        # A run still marked running was cut off by a crash or a kill.
        self.db.execute("UPDATE runs SET status = 'interrupted', ended = ? WHERE status = 'running'", (time.time(),))
        self.db.commit()

    def start_run(self, goal: str, model: str) -> int:
        cur = self.db.execute("INSERT INTO runs (started, goal, model) VALUES (?, ?, ?)", (time.time(), goal, model))
        self.db.commit()
        return int(cur.lastrowid)

    def add_event(self, run_id: int, seq: int, event: dict) -> None:
        self.db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (run_id, seq, event["ts"], event["kind"], event["title"], event["body"],
                         json.dumps(event["data"], default=str)))
        self.db.commit()

    def finish_run(self, run_id: int, status: str, summary: str) -> None:
        self.db.execute("UPDATE runs SET status = ?, ended = ?, summary = ? WHERE id = ?",
                        (status, time.time(), summary, run_id))
        self.db.commit()

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT id, started, goal, model, status, summary FROM runs ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(zip(("id", "started", "goal", "model", "status", "summary"), r)) for r in rows]

    def events(self, run_id: int) -> list[dict]:
        rows = self.db.execute("SELECT ts, kind, title, body, data FROM events WHERE run_id = ? ORDER BY seq",
                               (run_id,)).fetchall()
        return [{"ts": ts, "kind": kind, "title": title, "body": body, "data": json.loads(data)}
                for ts, kind, title, body, data in rows]

    # ---- the agent's own notes, per server ----
    def remember(self, server: str, topic: str, note: str) -> None:
        exists = self.db.execute("SELECT 1 FROM memory WHERE server = ? AND topic = ?", (server, topic)).fetchone()
        count = self.db.execute("SELECT COUNT(*) FROM memory WHERE server = ?", (server,)).fetchone()[0]
        if not exists and count >= MAX_NOTES:
            raise ValueError(f"Memory is full ({MAX_NOTES} notes). Forget one first.")
        self.db.execute("INSERT OR REPLACE INTO memory VALUES (?, ?, ?, ?)", (server, topic, note, time.time()))
        self.db.commit()

    def forget(self, server: str, topic: str) -> bool:
        cur = self.db.execute("DELETE FROM memory WHERE server = ? AND topic = ?", (server, topic))
        self.db.commit()
        return cur.rowcount > 0

    def notes(self, server: str) -> list[dict]:
        rows = self.db.execute("SELECT topic, note FROM memory WHERE server = ? ORDER BY updated", (server,)).fetchall()
        return [{"topic": t, "note": n} for t, n in rows]

    def clear_memory(self) -> None:
        self.db.execute("DELETE FROM memory")
        self.db.commit()

    # ---- crystal PvP training ----
    def pvp_state(self) -> dict:
        row = self.db.execute("SELECT data FROM pvp_state WHERE id = 1").fetchone()
        try:
            return json.loads(row[0]) if row else {}
        except json.JSONDecodeError:
            return {}

    def pvp_save_state(self, state: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO pvp_state VALUES (1, ?)", (json.dumps(state),))
        self.db.commit()

    def pvp_add_round(self, record: dict) -> None:
        self.db.execute("INSERT INTO pvp_rounds (ts, data) VALUES (?, ?)", (time.time(), json.dumps(record)))
        self.db.commit()

    def pvp_rounds(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT data FROM pvp_rounds ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def pvp_add_evaluation(self, generation: int, share: float) -> None:
        self.db.execute("INSERT INTO pvp_evaluations (ts, generation, share) VALUES (?, ?, ?)",
                        (time.time(), generation, share))
        self.db.commit()

    def pvp_evaluations(self) -> list[dict]:
        rows = self.db.execute("SELECT generation, share FROM pvp_evaluations ORDER BY id").fetchall()
        return [{"generation": g, "share": s} for g, s in rows]

    def pvp_add_lesson(self, lesson: str) -> None:
        self.db.execute("INSERT INTO pvp_lessons (ts, lesson) VALUES (?, ?)", (time.time(), lesson))
        self.db.commit()

    def pvp_lessons(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT lesson FROM pvp_lessons ORDER BY id").fetchall()]

    def pvp_reset(self) -> None:
        for table in ("pvp_state", "pvp_rounds", "pvp_evaluations", "pvp_lessons"):
            self.db.execute(f"DELETE FROM {table}")
        self.db.commit()

    def delete_all(self) -> None:
        self.db.execute("DELETE FROM runs")
        self.db.commit()
