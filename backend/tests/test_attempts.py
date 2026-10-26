import os
import sqlite3
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app

Runner = Callable[[str, str], str]


def decide(client: TestClient, kind: str, payout: float, claim_id: str = "CLM-1001"):
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": kind,
            "payout": payout,
            "explanation": "Covered.",
            "policy_section": "3. Trip Delay",
        },
    )


def pay(client: TestClient, amount: float, claim_id: str = "CLM-1001"):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def state(client: TestClient, claim_id: str = "CLM-1001") -> str:
    return client.get(f"/api/claims/{claim_id}").json()["state"]


def errors(client: TestClient) -> list[str]:
    steps = client.get("/api/claims/CLM-1001/steps").json()
    return [s["text"] for s in steps if s["kind"] == "error"]


def attempts() -> list[int]:
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        rows = conn.execute("SELECT failed FROM attempts ORDER BY id").fetchall()
    return [row[0] for row in rows]


def crash(claim_id: str, thread_id: str) -> str:
    raise RuntimeError("model unavailable")


def run_with(monkeypatch: pytest.MonkeyPatch, client: TestClient, runner: Runner):
    monkeypatch.setattr(main, "run_claim_agent", runner)
    return client.post("/api/claims/CLM-1001/run")


def test_a_crash_with_no_decision_goes_back_to_waiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run_with(monkeypatch, client, crash)
    assert state(client) == "waiting"
    assert attempts() == [1]


def test_a_second_failed_attempt_leaves_the_claim_for_a_handler(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run_with(monkeypatch, client, crash)
    run_with(monkeypatch, client, lambda claim_id, thread_id: "done")
    assert state(client) == "left_for_handler"
    assert attempts() == [1, 1]
    assert errors(client)[-1] == "The claim failed twice, so it is left for a handler."


def test_an_approval_that_was_not_paid_is_retried_and_paid_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def approve_only(claim_id: str, thread_id: str) -> str:
        decide(client, "approve", 420)
        return "done"

    def pay_only(claim_id: str, thread_id: str) -> str:
        assert decide(client, "approve", 420).status_code == 409
        assert pay(client, 420).status_code == 200
        assert pay(client, 420).status_code == 409
        return "done"

    run_with(monkeypatch, client, approve_only)
    assert state(client) == "waiting"
    assert errors(client) == ["The agent approved the claim but did not pay it."]
    run_with(monkeypatch, client, pay_only)
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["state"] == "decided"
    assert claim["payment"]["amount"] == 420.0
    assert attempts() == [1, 0]


def test_a_large_approval_that_fails_still_waits_for_sign_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def approve_then_crash(claim_id: str, thread_id: str) -> str:
        decide(client, "approve", 1250)
        raise RuntimeError("model unavailable")

    run_with(monkeypatch, client, approve_then_crash)
    assert state(client) == "waiting_for_sign_off"
    assert attempts() == [1]


def test_a_claim_left_for_a_handler_cannot_be_worked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def approve_only(claim_id: str, thread_id: str) -> str:
        decide(client, "approve", 420)
        return "done"

    run_with(monkeypatch, client, approve_only)
    run_with(monkeypatch, client, approve_only)
    assert state(client) == "left_for_handler"
    assert client.post("/api/claims/CLM-1001/run").status_code == 409
    assert decide(client, "decline", 0).status_code == 409
    assert pay(client, 420).status_code == 409
    assert client.get("/api/claims/CLM-1001").json()["payment"] is None
    assert state(client, "CLM-1002") == "waiting"


def test_startup_settles_a_claim_left_in_progress(client: TestClient):
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        conn.execute(
            "UPDATE claims SET state = 'in_progress' WHERE claim_id = 'CLM-1001'"
        )
        conn.execute(
            "INSERT INTO attempts (claim_id, started_at) VALUES ('CLM-1001', 'then')"
        )
    with TestClient(app) as restarted:
        assert state(restarted) == "waiting"
        assert errors(restarted) == ["The backend stopped during this attempt."]
    assert attempts() == [1]
