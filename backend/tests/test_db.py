import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import attempts, decisions, runs
from backend.db import connect, init_db


def test_a_connection_can_be_used_from_another_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """FastAPI opens a connection in one worker thread and uses it in another."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "threads.db"))
    init_db()
    conn = connect()
    with ThreadPoolExecutor(max_workers=1) as pool:
        row = pool.submit(
            lambda: conn.execute("SELECT COUNT(*) FROM claims").fetchone()
        ).result()
    assert row[0] == 25
    conn.close()


def test_a_connection_has_foreign_keys_wal_and_a_busy_timeout(client: TestClient):
    conn = connect()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
    finally:
        conn.close()


def test_the_database_refuses_what_makes_no_sense(client: TestClient):
    conn = connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO steps (claim_id, kind, text, created_at)"
                " VALUES ('CLM-9999', 'message', 'x', 'now')"
            )
        attempts.start_attempt(conn, "CLM-1001")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO attempts (claim_id, started_at) VALUES ('CLM-1001', 'now')"
            )
        runs.create(conn, 5)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (claim_limit, state, summary, started_at)"
                " VALUES (5, 'running', '', 'now')"
            )
    finally:
        conn.close()


def test_a_change_the_database_refuses_is_a_conflict_not_a_crash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def refuse(conn: sqlite3.Connection, claim_id: str, body: object) -> None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    monkeypatch.setattr(decisions, "pay", refuse)
    response = client.post("/api/claims/CLM-1001/payment", json={"amount": 420})
    assert response.status_code == 409
    assert response.json() == {"detail": "That conflicts with a change already made"}
