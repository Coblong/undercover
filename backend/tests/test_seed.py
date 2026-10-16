import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app

ALLOWED_FILES = {
    "claims.json",
    "policies.json",
    "customers.json",
    "handlers.json",
    "company.json",
    "policy_wording.md",
    "instructions.md",
}


def count(db: Path, table: str) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_seed_counts(client: TestClient, tmp_path: Path):
    db = tmp_path / "test.db"
    assert count(db, "claims") == 25
    assert count(db, "policies") == 50
    assert count(db, "customers") == 49
    assert count(db, "handlers") == 3


def test_starting_twice_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db = tmp_path / "twice.db"
    monkeypatch.setenv("UNDERCOVER_DB", str(db))
    for _ in range(2):
        with TestClient(app):
            pass
    assert count(db, "claims") == 25
    assert count(db, "policies") == 50


def test_only_allowed_files_are_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "spy.db"))
    read: set[str] = set()
    original = Path.read_text

    def spy(self: Path, *args: str, **kwargs: str) -> str:
        read.add(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    with TestClient(app) as client:
        for path in (
            "/api/claims/CLM-1001",
            "/api/policy-wording",
            "/api/instructions",
        ):
            client.get(path)
    assert {"claims.json", "policy_wording.md"} <= read
    assert read <= ALLOWED_FILES
