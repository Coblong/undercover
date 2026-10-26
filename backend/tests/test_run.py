from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import main


def steps(client: TestClient) -> list[dict[str, str]]:
    return client.get("/api/claims/CLM-1001/steps").json()


def state(client: TestClient, claim_id: str = "CLM-1001") -> str:
    return client.get(f"/api/claims/{claim_id}").json()["state"]


def test_run_starts_the_agent_and_returns_202(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    seen: dict[str, str] = {}

    def runner(claim_id: str, thread_id: str) -> str:
        seen["claim_id"] = claim_id
        seen["thread_id"] = thread_id
        seen["state"] = state(client)
        seen["second"] = str(client.post("/api/claims/CLM-1001/run").status_code)
        client.post(
            "/api/claims/CLM-1001/decision",
            json={
                "decision": "decline",
                "payout": 0,
                "explanation": "Not covered.",
                "policy_section": "9. General exclusions",
            },
        )
        return "done"

    monkeypatch.setattr(main, "run_claim_agent", runner)
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 202
    assert response.json()["state"] == "in_progress"
    assert seen["claim_id"] == "CLM-1001"
    assert seen["thread_id"].startswith("CLM-1001-")
    assert seen["state"] == "in_progress"
    assert seen["second"] == "409"
    assert state(client) == "decided"
    assert steps(client) == []


def test_run_that_crashes_leaves_the_claim_waiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def runner(claim_id: str, thread_id: str) -> str:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(main, "run_claim_agent", runner)
    assert client.post("/api/claims/CLM-1001/run").status_code == 202
    assert state(client) == "waiting"
    assert [(s["kind"], s["text"]) for s in steps(client)] == [
        ("error", "The agent stopped with an error (RuntimeError)")
    ]


def test_run_that_decides_nothing_goes_back_to_waiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "run_claim_agent", lambda claim_id, thread_id: "done")
    client.post("/api/claims/CLM-1001/run")
    assert state(client) == "waiting"
    assert steps(client)[0]["text"] == "The agent finished without deciding the claim."


def test_a_decided_claim_cannot_be_run(client: TestClient):
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "explanation": "Not covered.",
            "policy_section": "9. General exclusions",
        },
    )
    assert client.post("/api/claims/CLM-1001/run").status_code == 409


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


def test_claim_stays_in_progress_until_the_run_ends(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    during: dict[str, Any] = {}

    def runner(claim_id: str, thread_id: str) -> str:
        during["decided"] = decide(client, "approve", 420).json()
        during["state"] = state(client)
        during["again"] = decide(client, "decline", 0).status_code
        during["paid"] = client.post(
            "/api/claims/CLM-1001/payment", json={"amount": 420}
        )
        return "done"

    monkeypatch.setattr(main, "run_claim_agent", runner)
    client.post("/api/claims/CLM-1001/run")
    assert during["decided"]["state"] == "in_progress"
    assert "Now pay $420.00" in during["decided"]["message"]
    assert during["state"] == "in_progress"
    assert during["again"] == 409
    assert during["paid"].status_code == 200
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["state"] == "decided"
    assert claim["payment"]["amount"] == 420.0
    assert steps(client) == []


def test_large_approval_settles_to_waiting_for_sign_off_at_the_end(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    during: dict[str, Any] = {}

    def runner(claim_id: str, thread_id: str) -> str:
        during["message"] = decide(client, "approve", 1250, "CLM-1003").json()[
            "message"
        ]
        during["state"] = state(client, "CLM-1003")
        during["sign_off"] = client.post(
            "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        during["paid"] = client.post(
            "/api/claims/CLM-1003/payment", json={"amount": 1250}
        ).status_code
        return "done"

    monkeypatch.setattr(main, "run_claim_agent", runner)
    client.post("/api/claims/CLM-1003/run")
    assert "payment waits until a handler signs off" in during["message"]
    assert during["state"] == "in_progress"
    assert during["sign_off"] == 409
    assert during["paid"] == 409
    claim = client.get("/api/claims/CLM-1003").json()
    assert claim["state"] == "waiting_for_sign_off"
    assert claim["payment"] is None


def test_crash_after_a_decision_still_settles_the_claim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def runner(claim_id: str, thread_id: str) -> str:
        decide(client, "decline", 0)
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(main, "run_claim_agent", runner)
    client.post("/api/claims/CLM-1001/run")
    assert state(client) == "decided"
    assert [s["kind"] for s in steps(client)] == ["error"]
