"""SQLite store: every posting ever seen, its filter result, scores and alert state."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key TEXT PRIMARY KEY,
    source TEXT, source_id TEXT, company TEXT, title TEXT, url TEXT,
    location TEXT, work_model TEXT, location_label TEXT,
    salary_min REAL, salary_max REAL, salary_note TEXT, skills TEXT,
    posted_at TEXT, first_seen TEXT, last_seen TEXT, publisher TEXT,
    description TEXT,
    status TEXT,              -- filtered, candidate, triaged_low, scored, rejected_by_claude, applied, not_interested
    filter_reason TEXT,
    location_unclear INTEGER DEFAULT 0,
    triage_score REAL, triage_reason TEXT,
    rank REAL, fit_pct REAL, ats_pct REAL, seniority_pct REAL,
    verdict TEXT, gaps TEXT, hard_gaps TEXT, pitch TEXT, summary TEXT,
    alerted_at TEXT, digested_at TEXT, score_attempts INTEGER DEFAULT 0,
    employment_type TEXT
);
CREATE INDEX IF NOT EXISTS ix_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_first_seen ON jobs(first_seen);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "skills" not in cols:                     # databases created before the skills check
            self.db.execute("ALTER TABLE jobs ADD COLUMN skills TEXT")
        if "score_attempts" not in cols:
            self.db.execute("ALTER TABLE jobs ADD COLUMN score_attempts INTEGER DEFAULT 0")
        if "employment_type" not in cols:
            self.db.execute("ALTER TABLE jobs ADD COLUMN employment_type TEXT")

    # ---- meta ----
    def get_meta(self, k: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        self.db.commit()

    def is_empty(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0

    # ---- jobs ----
    def known(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM jobs WHERE key=?", (key,)).fetchone() is not None

    def touch(self, key: str) -> None:
        self.db.execute("UPDATE jobs SET last_seen=? WHERE key=?", (now_iso(), key))

    def insert(self, job: Job, fr) -> None:
        ts = now_iso()
        self.db.execute(
            """INSERT OR IGNORE INTO jobs(key,source,source_id,company,title,url,location,work_model,location_label,
               salary_min,salary_max,salary_note,posted_at,first_seen,last_seen,publisher,description,status,
               filter_reason,location_unclear,skills,employment_type)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.key, job.source, job.source_id, job.company, job.title, job.url, job.location_raw,
             fr.work_model, fr.location_label, job.salary_min, job.salary_max, fr.salary_note,
             job.posted_at.isoformat() if job.posted_at else None, ts, ts, job.publisher,
             job.description, fr.status, fr.reason if fr.status == "filtered" else "", int(fr.location_unclear),
             ", ".join(fr.skills), job.employment_type),
        )

    def prune(self, before_iso: str) -> int:
        """Drop the stored posting text of old jobs that never became candidates. The row itself stays,
        so the job is still recognized as already seen."""
        cur = self.db.execute(
            "UPDATE jobs SET description='' WHERE first_seen<? AND description!='' "
            "AND status IN ('filtered','triaged_low','rejected_by_claude','not_interested')", (before_iso,))
        self.db.commit()
        if cur.rowcount > 200:
            self.db.execute("VACUUM")
        return cur.rowcount

    def update(self, key: str, **fields) -> None:
        if not fields:
            return
        for k, v in list(fields.items()):
            if isinstance(v, (list, dict)):
                fields[k] = json.dumps(v)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE key=?", (*fields.values(), key))

    def commit(self) -> None:
        self.db.commit()

    def rows(self, where: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.db.execute(f"SELECT * FROM jobs WHERE {where}", params).fetchall()

    def stats_since(self, since_iso: str) -> dict:
        q = "SELECT status, COUNT(*) n FROM jobs WHERE first_seen>=? GROUP BY status"
        return {r["status"]: r["n"] for r in self.db.execute(q, (since_iso,))}

    def filter_reasons_since(self, since_iso: str, limit: int = 8) -> list[tuple[str, int]]:
        q = """SELECT filter_reason r, COUNT(*) n FROM jobs WHERE first_seen>=? AND status='filtered'
               GROUP BY r ORDER BY n DESC LIMIT ?"""
        return [(r["r"], r["n"]) for r in self.db.execute(q, (since_iso, limit))]
