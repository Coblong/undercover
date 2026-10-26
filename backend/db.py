"""SQLite storage, seeded from the business data in data/."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = Path(__file__).resolve().parent / "undercover.db"

SCHEMA = (Path(__file__).resolve().parent / "schema.sql").read_text()


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
        os.getenv("UNDERCOVER_DB", DEFAULT_DB),
        timeout=10,
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run a block as one transaction that takes the write lock before it reads.

    Two requests that check and then write cannot both pass the check.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def get_db() -> Iterator[sqlite3.Connection]:
    """Give a request its own connection."""
    with closing(connect()) as conn:
        yield conn


def init_db() -> None:
    """Create the tables and, when the database is empty, load the data."""
    with closing(connect()) as conn:
        conn.executescript(SCHEMA)
        with transaction(conn):
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
