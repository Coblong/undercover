from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import seed


def test_reseeding_starts_again_from_the_data(
    client: TestClient, capsys: pytest.CaptureFixture[str]
):
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "explanation": "Not covered.",
            "policy_section": "9. General exclusions",
        },
    )
    assert client.get("/api/claims/CLM-1001").json()["state"] == "decided"
    seed.main()
    assert client.get("/api/claims/CLM-1001").json()["state"] == "waiting"
    assert "25 claims, 50 policies, 49 customers, 3 handlers" in capsys.readouterr().out


def test_reseeding_an_empty_database_creates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "new.db"))
    seed.main()
    assert "25 claims" in capsys.readouterr().out
