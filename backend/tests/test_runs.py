from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import Agents

DISPATCH = {"concurrency": 3}
SECTION = "3. Trip Delay"


def create(client: TestClient, **changes: Any) -> dict[str, Any]:
    response = client.post("/api/runs/dispatch", json={**DISPATCH, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def plan(client: TestClient, run_id: int) -> list[str]:
    return client.post(f"/api/runs/{run_id}/plan").json()["claim_ids"]


def start(client: TestClient, run_id: int, claim_id: str) -> Any:
    return client.post(f"/api/runs/{run_id}/claims/{claim_id}/start")


def end(client: TestClient, claim_id: str, error: str | None = None) -> Any:
    return client.post(f"/api/claims/{claim_id}/attempt/end", json={"error": error})


def decide(client: TestClient, claim_id: str, kind: str, payout: float) -> None:
    body = {
        "decision": kind,
        "payout": payout,
        "explanation": "Stub.",
        "policy_section": SECTION,
    }
    assert client.post(f"/api/claims/{claim_id}/decision", json=body).status_code == 200


def latest(client: TestClient) -> dict[str, Any]:
    return client.get("/api/dispatches/latest").json()


def test_a_dispatch_is_created_running_with_its_settings(client: TestClient):
    assert client.get("/api/dispatches/latest").json() is None
    run = create(client, concurrency=7)
    assert run["state"] == "running"
    assert (run["concurrency"], run["model"], run["reasoning_effort"]) == (
        7,
        "openai/gpt-6-luna",
        "low",
    )
    assert run["config_version"] == 1
    assert run["claims"] == []
    assert run["summary"] is None
    assert latest(client)["run_id"] == run["run_id"]


def test_only_one_dispatch_runs_at_a_time(client: TestClient):
    run = create(client)
    again = client.post("/api/runs/dispatch", json=DISPATCH)
    assert again.status_code == 409
    assert "already running" in again.json()["detail"]
    ended = client.post(f"/api/runs/{run['run_id']}/end", json={"state": "finished"})
    assert ended.json()["state"] == "finished"
    assert ended.json()["ended_at"]
    assert create(client)["run_id"] == run["run_id"] + 1


def test_a_run_ends_once(client: TestClient):
    run_id = create(client)["run_id"]
    assert client.post(f"/api/runs/{run_id}/end", json={"state": "failed"}).json()[
        "state"
    ] == ("failed")
    again = client.post(f"/api/runs/{run_id}/end", json={"state": "finished"})
    assert again.status_code == 409
    assert client.post("/api/runs/99/end", json={"state": "failed"}).status_code == 404


def test_the_plan_is_every_claim_to_work_oldest_submitted_first(client: TestClient):
    claims = client.get("/api/claims").json()
    oldest_first = [
        c["claim_id"]
        for c in sorted(claims, key=lambda c: (c["submitted_on"], c["claim_id"]))
    ]
    run_id = create(client)["run_id"]
    assert plan(client, run_id) == oldest_first
    assert plan(client, run_id) == oldest_first
    assert [c["claim_id"] for c in latest(client)["claims"]] == oldest_first


def test_the_plan_mixes_in_claims_in_progress_and_leaves_out_the_rest(
    client: TestClient,
):
    decide(client, "CLM-1001", "decline", 0)
    client.post("/api/claims/CLM-1002/run")
    first = create(client)["run_id"]
    chosen = plan(client, first)
    assert "CLM-1001" not in chosen
    assert "CLM-1002" in chosen
    assert len(chosen) == 24
    ordered = sorted(
        (c for c in client.get("/api/claims").json() if c["claim_id"] in chosen),
        key=lambda c: (c["submitted_on"], c["claim_id"]),
    )
    assert chosen == [c["claim_id"] for c in ordered]


def test_a_waiting_claim_starts_and_one_in_progress_carries_on(client: TestClient):
    run_id = create(client)["run_id"]
    plan(client, run_id)
    first = start(client, run_id, "CLM-1001").json()
    assert first["resumed"] is False
    assert first["thread_id"].startswith("CLM-1001-")
    assert client.get("/api/claims/CLM-1001").json()["state"] == "in_progress"
    again = start(client, run_id, "CLM-1001").json()
    assert again == {**first, "resumed": True}
    started = [c for c in latest(client)["claims"] if c["started_at"]]
    assert [c["claim_id"] for c in started] == ["CLM-1001"]


def test_a_claim_can_only_be_started_in_a_running_run_that_holds_it(
    client: TestClient,
):
    run_id = create(client)["run_id"]
    assert start(client, run_id, "CLM-1001").status_code == 409
    plan(client, run_id)
    decide(client, "CLM-1002", "decline", 0)
    assert start(client, run_id, "CLM-1002").status_code == 409
    assert start(client, run_id, "CLM-9999").status_code == 404
    assert start(client, 99, "CLM-1001").status_code == 404
    client.post(f"/api/runs/{run_id}/end", json={"state": "finished"})
    assert start(client, run_id, "CLM-1001").status_code == 409


def test_a_carried_on_claim_keeps_its_attempt_from_an_earlier_run(client: TestClient):
    first = create(client)["run_id"]
    plan(client, first)
    thread = start(client, first, "CLM-1001").json()["thread_id"]
    client.post("/api/runs/stop")
    second = create(client)["run_id"]
    assert "CLM-1001" in plan(client, second)
    carried = start(client, second, "CLM-1001").json()
    assert carried["thread_id"] == thread
    assert carried["resumed"] is True


def test_ending_an_attempt_settles_the_claim(client: TestClient):
    run_id = create(client)["run_id"]
    plan(client, run_id)
    assert end(client, "CLM-1001").status_code == 409
    start(client, run_id, "CLM-1001")
    decide(client, "CLM-1001", "decline", 0)
    ended = end(client, "CLM-1001")
    assert ended.json()["state"] == "decided"


def test_a_failed_attempt_goes_back_to_waiting_and_two_leave_the_claim(
    client: TestClient,
):
    run_id = create(client)["run_id"]
    plan(client, run_id)
    start(client, run_id, "CLM-1001")
    assert end(client, "CLM-1001", "The agent stopped with an error").json()[
        "state"
    ] == ("waiting")
    assert start(client, run_id, "CLM-1001").json()["resumed"] is False
    assert end(client, "CLM-1001", "again").json()["state"] == "left_for_handler"


def test_starting_the_service_stops_every_run_that_is_going(client: TestClient):
    run_id = create(client)["run_id"]
    assert client.post("/api/runs/stop").json() == {"stopped": 1, "stopped_rounds": 0}
    assert latest(client)["state"] == "stopped"
    assert latest(client)["run_id"] == run_id
    assert client.post("/api/runs/stop").json() == {"stopped": 0, "stopped_rounds": 0}
    assert create(client)["run_id"] == run_id + 1


def test_the_summary_is_built_from_the_records(client: TestClient):
    run_id = create(client)["run_id"]
    plan(client, run_id)
    for number in range(1, 7):
        start(client, run_id, f"CLM-100{number}")
    decide(client, "CLM-1001", "approve", 420)
    client.post("/api/claims/CLM-1001/payment", json={"amount": 420})
    decide(client, "CLM-1002", "decline", 0)
    decide(client, "CLM-1003", "approve", 1250)
    decide(client, "CLM-1004", "refer", 0)
    for number in (1, 2, 3, 4):
        assert end(client, f"CLM-100{number}").status_code == 200
    end(client, "CLM-1005", "x")
    start(client, run_id, "CLM-1005")
    end(client, "CLM-1005", "y")
    assert latest(client)["summary"] is None
    client.post(f"/api/runs/{run_id}/end", json={"state": "finished"})
    summary = latest(client)["summary"]
    assert summary == {
        "approved": 2,
        "declined": 1,
        "referred": 1,
        "waiting_for_sign_off": [{"claim_id": "CLM-1003", "payout": 1250.0}],
        "left_for_handler": ["CLM-1005"],
        "in_progress": ["CLM-1006"],
    }
    states = {c["claim_id"]: c["state"] for c in latest(client)["claims"]}
    assert states["CLM-1003"] == "waiting_for_sign_off"
    assert states["CLM-1010"] == "waiting"


def test_cost_and_time_are_the_runs_own(client: TestClient):
    first = create(client)["run_id"]
    plan(client, first)
    for run_id, claim_id, cost in (
        (first, "CLM-1001", 0.01),
        (first, "CLM-1001", 0.02),
        (first, "CLM-1002", 0.04),
        (None, "CLM-1001", 0.5),
    ):
        body = {"run_id": run_id, "input_tokens": 10, "output_tokens": 5, "cost": cost}
        assert (
            client.post(f"/api/claims/{claim_id}/model-calls", json=body).status_code
            == 200
        )
    for run_id, claim_id, seconds in (
        (first, "CLM-1001", 3.0),
        (first, "CLM-1001", 2.0),
        (first, "CLM-1002", 4.0),
        (None, "CLM-1001", 90.0),
    ):
        body = {"run_id": run_id, "seconds": seconds}
        assert (
            client.post(f"/api/claims/{claim_id}/work-times", json=body).status_code
            == 200
        )
    run = latest(client)
    assert run["cost"] == pytest.approx(0.07)
    assert run["claim_seconds"] == 9.0
    by_claim = {c["claim_id"]: c for c in run["claims"]}
    assert (by_claim["CLM-1001"]["cost"], by_claim["CLM-1001"]["seconds"]) == (
        0.03,
        5.0,
    )
    assert by_claim["CLM-1003"]["cost"] == 0
    client.post(f"/api/runs/{first}/end", json={"state": "finished"})
    second = create(client)["run_id"]
    body = {"run_id": second, "input_tokens": 1, "output_tokens": 1, "cost": 1.0}
    client.post("/api/claims/CLM-1001/model-calls", json=body)
    assert latest(client)["cost"] == 1.0
    assert client.get("/api/dispatches/latest").json()["run_id"] == second


def test_starting_a_dispatch_asks_the_service_with_the_number_of_claim_agents(
    client: TestClient, agents: Agents
):
    asked: list[int] = []

    def starter(concurrency: int) -> int:
        asked.append(concurrency)
        return create(client, concurrency=concurrency)["run_id"]

    agents.dispatch(starter)
    response = client.post("/api/dispatches", json={"concurrency": 9})
    assert response.status_code == 202
    assert asked == [9]
    assert response.json()["concurrency"] == 9
    assert response.json()["state"] == "running"


def test_a_start_the_service_refuses_is_refused_with_its_reason(
    client: TestClient, agents: Agents
):
    def starter(concurrency: int) -> int:
        raise HTTPException(409, "A dispatch or an evaluation is already running")

    agents.dispatch(starter)
    response = client.post("/api/dispatches", json={"concurrency": 3})
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
    assert client.post("/api/dispatches", json={"concurrency": 26}).status_code == 422
    assert client.post("/api/dispatches", json={"concurrency": 0}).status_code == 422


def test_a_service_that_is_not_running_says_so(client: TestClient):
    response = client.post("/api/dispatches", json={"concurrency": 3})
    assert response.status_code == 503
    assert response.json()["detail"] == "The agent's service could not be reached."
