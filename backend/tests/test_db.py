from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

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
