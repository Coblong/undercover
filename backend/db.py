"""Storage on SQLite or Postgres, seeded from the business data in data/.

The queries are SQLAlchemy Core, so each is written once. `DATABASE_URL` selects
Supabase; without it the backend uses the SQLite file in `UNDERCOVER_DB`.
"""

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, insert, select, text
from sqlalchemy.pool import ConnectionPoolEntry

from backend import tables as t

DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DB = Path(__file__).resolve().parent / "undercover.db"

SCHEMA = "undercover"
POOL_SIZE = 3
POOL_OVERFLOW = 1

_engines: dict[str, Engine] = {}
_lock = threading.Lock()


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


def database_url() -> str:
    """Return the database to use: Supabase when DATABASE_URL is set, else SQLite."""
    url = os.getenv("DATABASE_URL")
    if url:
        scheme, _, rest = url.partition("://")
        return f"postgresql+psycopg://{rest}" if scheme.startswith("postgres") else url
    return f"sqlite:///{os.getenv('UNDERCOVER_DB', DEFAULT_DB)}"


def sqlite_connect(
    dbapi_connection: sqlite3.Connection, _: ConnectionPoolEntry
) -> None:
    """Take over transactions from the driver and turn on foreign keys and WAL."""
    dbapi_connection.isolation_level = None
    dbapi_connection.execute("PRAGMA foreign_keys = ON")
    dbapi_connection.execute("PRAGMA journal_mode = WAL")


def sqlite_begin(conn: Connection) -> None:
    """Begin a write transaction by taking the write lock before it reads."""
    conn.exec_driver_sql("BEGIN IMMEDIATE" if conn.info.get("write") else "BEGIN")


def make_engine(url: str) -> Engine:
    """Build an engine that holds at most four connections to Postgres."""
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"timeout": 10})
        event.listen(engine, "connect", sqlite_connect)
        event.listen(engine, "begin", sqlite_begin)
        return engine
    return create_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=POOL_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"options": f"-c search_path={SCHEMA}"},
    )


def engine() -> Engine:
    """Return the engine for the current settings, one for each database."""
    url = database_url()
    with _lock:
        if url not in _engines:
            _engines[url] = make_engine(url)
        return _engines[url]


def dispose_engines() -> None:
    """Close every connection, for tests that change the database."""
    with _lock:
        for built in _engines.values():
            built.dispose()
        _engines.clear()


def connect() -> Connection:
    """Open a connection to the database."""
    return engine().connect()


@contextmanager
def transaction(conn: Connection) -> Iterator[None]:
    """Run a block as one transaction.

    On SQLite it takes the write lock before it reads. On Postgres the queries
    that check before they write lock the row with `FOR UPDATE`. Either way two
    requests cannot both pass a check.
    """
    if conn.in_transaction():
        conn.commit()
    conn.info["write"] = True
    try:
        with conn.begin():
            yield
    finally:
        conn.info["write"] = False


def get_db() -> Iterator[Connection]:
    """Give a request its own connection."""
    with closing(connect()) as conn:
        yield conn


def init_db() -> None:
    """Create the tables and, when the database is empty, load the data."""
    built = engine()
    if built.dialect.name == "postgresql":
        with built.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    t.metadata.create_all(built)
    with closing(connect()) as conn, transaction(conn):
        if conn.execute(select(t.claims.c.claim_id).limit(1)).first() is None:
            seed(conn)


def reset_db() -> None:
    """Drop our tables and the agent's saved progress, then seed again."""
    built = engine()
    if built.dialect.name == "postgresql":
        with built.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    else:
        t.metadata.drop_all(built)
    init_db()


def seed(conn: Connection) -> None:
    """Load claims, policies, customers and handlers. Every claim starts waiting."""
    conn.execute(
        insert(t.claims),
        [
            {
                **{c.name: claim[c.name] for c in t.claims.c if c.name != "state"},
                "state": "waiting",
            }
            for claim in load("claims.json")
        ],
    )
    conn.execute(
        insert(t.policies),
        [{"policy_id": p["policy_id"], "data": p} for p in load("policies.json")],
    )
    conn.execute(
        insert(t.customers),
        [
            {
                "customer_id": c["customer_id"],
                "name": c["name"],
                "previous_claims": c["previous_claims"],
            }
            for c in load("customers.json")
        ],
    )
    conn.execute(
        insert(t.handlers),
        [
            {
                "handler_id": h["handler_id"],
                "name": h["name"],
                "role": h["role"],
                "can_approve_over_limit": bool(h["can_approve_over_limit"]),
            }
            for h in load("handlers.json")
        ],
    )
