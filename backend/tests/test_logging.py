import logging
import traceback

import pytest
from fastapi.testclient import TestClient

from agent.settings import AgentError
from backend import main


def crash_with(exc: Exception):
    def runner(claim_id: str, thread_id: str) -> str:
        raise exc

    return runner


def test_a_crash_is_logged_in_full_and_shown_without_details(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    secret = RuntimeError(
        "cannot open /Users/someone/app.py via https://provider.example/key"
    )
    monkeypatch.setattr(main, "run_claim_agent", crash_with(secret))
    with caplog.at_level(logging.INFO, logger="backend.main"):
        client.post("/api/claims/CLM-1001/run")

    failure = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert failure.exc_info is not None
    assert "/Users/someone/app.py" in "".join(
        traceback.format_exception(*failure.exc_info)
    )
    shown = client.get("/api/claims/CLM-1001/steps").json()[0]["text"]
    assert shown == "The agent stopped with an error (RuntimeError)"
    assert "/Users" not in shown
    assert "https" not in shown


def test_a_message_the_agent_wrote_for_the_team_is_shown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "run_claim_agent", crash_with(AgentError("no key set")))
    client.post("/api/claims/CLM-1001/run")
    assert client.get("/api/claims/CLM-1001/steps").json()[0]["text"] == (
        "The agent stopped with an error: no key set"
    )


def test_every_ended_attempt_logs_one_line_with_its_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(main, "run_claim_agent", crash_with(RuntimeError("x")))
    with caplog.at_level(logging.INFO, logger="backend.main"):
        client.post("/api/claims/CLM-1001/run")
        client.post("/api/claims/CLM-1002/run")
    lines = [
        r.getMessage() for r in caplog.records if "Attempt ended" in r.getMessage()
    ]
    assert len(lines) == 2
    assert lines[0].startswith(
        "Attempt ended: claim=CLM-1001 run=None state=waiting failed=True seconds="
    )


def test_a_queue_agent_crash_is_logged(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    def crash(run_id: int) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(main, "run_queue_agent", crash)
    client.post("/api/runs", json={"limit": 1})
    assert any("queue agent failed in run 1" in r.getMessage() for r in caplog.records)
