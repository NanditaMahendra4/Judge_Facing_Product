"""SQLite storage: cases, filings, briefs, and an audit log.

Everything stays on the host running the app. Nothing is written outside
``BENCHBRIEF_DB`` (default ``./benchbrief.db``).
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY,
    number TEXT UNIQUE NOT NULL,
    caption TEXT NOT NULL,
    brief_enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS filings (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    filer TEXT NOT NULL,
    title TEXT NOT NULL,
    filed_on TEXT NOT NULL,
    doc_json TEXT NOT NULL,
    brief_json TEXT,
    guard_json TEXT,
    extractor TEXT,
    seconds REAL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    case_id INTEGER,
    filing_id INTEGER,
    detail TEXT
);
"""


def db_path() -> str:
    return os.environ.get("BENCHBRIEF_DB", "benchbrief.db")


@contextmanager
def connect(path: str | None = None):
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def audit(conn, event: str, case_id: int | None = None, filing_id: int | None = None, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit(at, event, case_id, filing_id, detail) VALUES (?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), event, case_id, filing_id, json.dumps(detail or {})),
    )


def upsert_case(conn, number: str, caption: str) -> int:
    row = conn.execute("SELECT id FROM cases WHERE number=?", (number,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO cases(number, caption) VALUES (?,?)", (number, caption)).lastrowid


def set_enabled(conn, case_id: int, enabled: bool) -> None:
    conn.execute("UPDATE cases SET brief_enabled=? WHERE id=?", (1 if enabled else 0, case_id))
    audit(conn, "brief_enabled" if enabled else "brief_disabled", case_id=case_id)
