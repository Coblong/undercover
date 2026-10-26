import os
import sqlite3
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from backend import attempts as attempt_rules
from backend.db import connect
from backend.main import app
from conftest import Agents

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


def run_with(agents: Agents, client: TestClient, runner: Runner):
    agents.claim(runner)
    return client.post("/api/claims/CLM-1001/run")


def test_a_crash_with_no_decision_goes_back_to_waiting(
    client: TestClient, agents: Agents
):
    run_with(agents, client, crash)
    assert state(client) == "waiting"
    assert attempts() == [1]


def test_a_second_failed_attempt_leaves_the_claim_for_a_handler(
    client: TestClient, agents: Agents
):
    run_with(agents, client, crash)
    run_with(agents, client, lambda claim_id, thread_id: "done")
    assert state(client) == "left_for_handler"
    assert attempts() == [1, 1]
    assert errors(client)[-1] == "The claim failed twice, so it is left for a handler."


def test_an_approval_that_was_not_paid_is_retried_and_paid_once(
    client: TestClient, agents: Agents
):
    def approve_only(claim_id: str, thread_id: str) -> str:
        decide(client, "approve", 420)
        return "done"

    def pay_only(claim_id: str, thread_id: str) -> str:
        assert decide(client, "approve", 420).status_code == 409
        assert pay(client, 420).status_code == 200
        assert pay(client, 420).status_code == 409
        return "done"

    run_with(agents, client, approve_only)
    assert state(client) == "waiting"
    assert errors(client) == ["The agent approved the claim but did not pay it."]
    run_with(agents, client, pay_only)
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["state"] == "decided"
    assert claim["payment"]["amount"] == 420.0
    assert attempts() == [1, 0]


def test_a_large_approval_that_fails_still_waits_for_sign_off(
    client: TestClient, agents: Agents
):
    def approve_then_crash(claim_id: str, thread_id: str) -> str:
        decide(client, "approve", 1100, "CLM-1002")
        raise RuntimeError("model unavailable")

    agents.claim(approve_then_crash)
    client.post("/api/claims/CLM-1002/run")
    assert state(client, "CLM-1002") == "waiting_for_sign_off"
    assert attempts() == [1]


def test_a_claim_left_for_a_handler_cannot_be_worked(
    client: TestClient, agents: Agents
):
    def approve_only(claim_id: str, thread_id: str) -> str:
        decide(client, "approve", 420)
        return "done"

    run_with(agents, client, approve_only)
    run_with(agents, client, approve_only)
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


def test_a_failure_inside_end_attempt_changes_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    conn = connect()
    attempt_rules.start_attempt(conn, "CLM-1001")

    def fail(conn: sqlite3.Connection, claim_id: str) -> int:
        raise RuntimeError("database is locked")

    real = attempt_rules.failed_attempts
    monkeypatch.setattr(attempt_rules, "failed_attempts", fail)
    with pytest.raises(RuntimeError):
        attempt_rules.end_attempt(conn, "CLM-1001", "The agent crashed.")
    conn.close()
    assert state(client) == "in_progress"
    assert errors(client) == []
    assert attempts() == [None]
    monkeypatch.setattr(attempt_rules, "failed_attempts", real)
    with TestClient(app) as restarted:
        assert state(restarted) == "waiting"
        assert errors(restarted) == ["The backend stopped during this attempt."]
    assert attempts() == [1]
