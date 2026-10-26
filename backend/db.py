"""SQLite storage, seeded from the business data in data/."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = Path(__file__).resolve().parent / "undercover.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    submitted_on TEXT NOT NULL,
    incident_date TEXT NOT NULL,
    incident_location TEXT NOT NULL,
    summary TEXT NOT NULL,
    story TEXT NOT NULL,
    items TEXT NOT NULL,
    documents TEXT NOT NULL,
    amount_claimed REAL NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
    policy_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    previous_claims TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handlers (
    handler_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    can_approve_over_limit INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    claim_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    payout REAL NOT NULL,
    explanation TEXT NOT NULL,
    policy_section TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    claim_id TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    paid_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sign_offs (
    claim_id TEXT PRIMARY KEY,
    handler_id TEXT NOT NULL,
    signed_off_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    run_id INTEGER,
    started_at TEXT NOT NULL,
    failed INTEGER
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    """Return the current UTC time as an ISO string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def load(name: str) -> Any:
    """Read one JSON file from data/."""
    return json.loads((DATA / name).read_text())


@cache
def payout_limit() -> float:
    """Return the payout a handler must sign off above."""
    return float(load("company.json")["payout_approval_limit"])


def connect() -> sqlite3.Connection:
    """Open a connection to the database named by UNDERCOVER_DB."""
    conn = sqlite3.connect(
        os.getenv("UNDERCOVER_DB", DEFAULT_DB), timeout=10, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def get_db() -> Iterator[sqlite3.Connection]:
    """Give a request its own connection."""
    with closing(connect()) as conn:
        yield conn


def init_db() -> None:
    """Create the tables and, when the database is empty, load the data."""
    with closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT 1 FROM claims").fetchone() is None:
            seed(conn)


def seed(conn: sqlite3.Connection) -> None:
    """Load claims, policies, customers and handlers. Every claim starts waiting."""
    claims = load("claims.json")
    policies = load("policies.json")
    customers = load("customers.json")
    handlers = load("handlers.json")
    conn.executemany(
        "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting')",
        [
            (
                c["claim_id"],
                c["policy_id"],
                c["customer_id"],
                c["claim_type"],
                c["submitted_on"],
                c["incident_date"],
                c["incident_location"],
                c["summary"],
                c["story"],
                json.dumps(c["items"]),
                json.dumps(c["documents"]),
                c["amount_claimed"],
            )
            for c in claims
        ],
    )
    conn.executemany(
        "INSERT INTO policies VALUES (?, ?)",
        [(p["policy_id"], json.dumps(p)) for p in policies],
    )
    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [
            (c["customer_id"], c["name"], json.dumps(c["previous_claims"]))
            for c in customers
        ],
    )
    conn.executemany(
        "INSERT INTO handlers VALUES (?, ?, ?, ?)",
        [
            (h["handler_id"], h["name"], h["role"], int(h["can_approve_over_limit"]))
            for h in handlers
        ],
    )
