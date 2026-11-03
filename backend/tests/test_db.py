from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Executable, insert, select, text
from sqlalchemy.exc import IntegrityError

from backend import attempts, db, decisions, runs
from backend import tables as t
from backend.db import connect, init_db


def test_a_connection_can_be_used_from_another_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """FastAPI opens a connection in one worker thread and uses it in another."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "threads.db"))
    init_db()
    with closing(connect()) as conn, ThreadPoolExecutor(max_workers=1) as pool:
        count = pool.submit(
            lambda: conn.execute(text("SELECT COUNT(*) FROM claims")).scalar_one()
        ).result()
    assert count == 25


def test_a_connection_has_foreign_keys_wal_and_a_busy_timeout(client: TestClient):
    with closing(connect()) as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar_one() == 10000


def test_the_database_refuses_what_makes_no_sense(client: TestClient):
    def refuses(conn: Connection, statement: Executable) -> None:
        with pytest.raises(IntegrityError):
            conn.execute(statement)
        conn.rollback()

    with closing(connect()) as conn:
        refuses(
            conn,
            insert(t.steps).values(
                claim_id="CLM-9999", kind="message", text="x", created_at="now"
            ),
        )
        attempts.start_attempt(conn, "CLM-1001")
        refuses(conn, insert(t.attempts).values(claim_id="CLM-1001", started_at="now"))
        runs.create(conn, 5)
        refuses(
            conn,
            insert(t.runs).values(
                claim_limit=5, state="running", summary="", started_at="now"
            ),
        )


def test_a_change_the_database_refuses_is_a_conflict_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def refuse(conn: Connection, claim_id: str, body: object) -> None:
        raise IntegrityError("UNIQUE constraint failed", None, Exception())

    monkeypatch.setattr(decisions, "pay", refuse)
    response = client.post("/api/claims/CLM-1001/payment", json={"amount": 420})
    assert response.status_code == 409
    assert response.json() == {"detail": "That conflicts with a change already made"}


def test_supabase_is_used_only_when_database_url_is_set(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("UNDERCOVER_DB", "/tmp/x.db")  # noqa: S108
    assert db.database_url() == "sqlite:////tmp/x.db"
    monkeypatch.setenv("DATABASE_URL", "")
    assert db.database_url().startswith("sqlite")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u.ref:pw@host:5432/postgres")
    assert db.database_url() == "postgresql+psycopg://u.ref:pw@host:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", "postgres://u:pw@host/postgres")
    assert db.database_url() == "postgresql+psycopg://u:pw@host/postgres"


def test_postgres_gets_a_small_pool_and_its_own_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    built: dict[str, Any] = {}

    def fake(url: str, **kwargs: Any) -> str:
        built.update(kwargs)
        return "engine"

    monkeypatch.setattr(db, "create_engine", fake)
    db.make_engine("postgresql+psycopg://u:pw@localhost:5432/postgres")
    assert built["pool_size"] + built["max_overflow"] == 4
    assert built["connect_args"] == {"options": "-c search_path=undercover"}


def test_the_tests_never_use_supabase(client: TestClient):
    assert db.database_url().startswith("sqlite")
    with closing(connect()) as conn:
        assert conn.execute(select(t.claims.c.claim_id)).first() is not None
