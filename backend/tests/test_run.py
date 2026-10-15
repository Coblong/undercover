import pytest
from fastapi.testclient import TestClient

from backend import main


def steps(client: TestClient) -> list[dict[str, str]]:
    return client.get("/api/claims/CLM-1001/steps").json()


def state(client: TestClient) -> str:
    return client.get("/api/claims/CLM-1001").json()["state"]


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
        ("error", "The agent stopped with an error: model unavailable")
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
