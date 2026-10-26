import os
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend import main, runs
from backend.db import connect
from backend.main import app


def new_run(limit: int) -> int:
    with closing(connect()) as conn:
        return runs.create(conn, limit).run_id


def work(client: TestClient, run_id: int, claim_id: str):
    return client.post(f"/api/runs/{run_id}/claims/{claim_id}/work")


def finish(client: TestClient, run_id: int, summary: str = "All done."):
    return client.post(f"/api/runs/{run_id}/finish", json={"summary": summary})


def latest(client: TestClient):
    return client.get("/api/runs/latest").json()


def decide(client: TestClient, claim_id: str, kind: str, payout: float):
    client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": kind,
            "payout": payout,
            "explanation": "Covered.",
            "policy_section": "3. Trip Delay",
        },
    )


@pytest.fixture
def declining(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    def runner(claim_id: str, thread_id: str) -> str:
        decide(client, claim_id, "decline", 0)
        return "done"

    monkeypatch.setattr(main, "run_claim_agent", runner)


def states(client: TestClient) -> list[str]:
    return [c["state"] for c in client.get("/api/claims").json()]


def test_start_a_run_that_the_agent_never_finishes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    seen: list[int] = []
    monkeypatch.setattr(main, "run_queue_agent", seen.append)
    response = client.post("/api/runs", json={"limit": 5})
    assert response.status_code == 202
    assert response.json()["limit"] == 5
    assert seen == [response.json()["run_id"]]
    assert latest(client)["state"] == "failed"
    assert latest(client)["summary"] == "The agent stopped without finishing."


def test_a_queue_agent_that_crashes_fails_the_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def crash(run_id: int) -> None:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(main, "run_queue_agent", crash)
    client.post("/api/runs", json={"limit": 5})
    assert latest(client)["summary"] == (
        "The queue agent stopped with an error: model unavailable"
    )


def test_a_run_needs_a_limit_and_only_one_runs_at_a_time(client: TestClient):
    assert client.post("/api/runs", json={"limit": 0}).status_code == 422
    assert client.post("/api/runs", json={}).status_code == 422
    assert client.get("/api/runs/latest").json() is None
    new_run(3)
    assert client.post("/api/runs", json={"limit": 5}).status_code == 409


def test_the_limit_stops_a_run_starting_more_claims(
    client: TestClient, declining: None
):
    run_id = new_run(2)
    assert work(client, run_id, "CLM-1001").status_code == 200
    assert work(client, run_id, "CLM-1002").status_code == 200
    refused = work(client, run_id, "CLM-1003")
    assert refused.status_code == 409
    assert "limit of 2 claims" in refused.json()["detail"]
    assert states(client).count("waiting") == 23


def test_the_result_is_one_line_from_the_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def runner(claim_id: str, thread_id: str) -> str:
        if claim_id == "CLM-1001":
            decide(client, claim_id, "approve", 420)
            client.post(f"/api/claims/{claim_id}/payment", json={"amount": 420})
        elif claim_id == "CLM-1002":
            decide(client, claim_id, "approve", 1100)
        else:
            decide(client, claim_id, "refer", 0)
        return "I did something else entirely."

    monkeypatch.setattr(main, "run_claim_agent", runner)
    run_id = new_run(5)
    lines = [work(client, run_id, f"CLM-100{n}").json()["message"] for n in (1, 2, 3)]
    assert lines == [
        "CLM-1001 decided: approve $420.00, paid.",
        "CLM-1002 decided: approve $1,100.00, waiting for a handler's sign-off.",
        "CLM-1003 decided: refer.",
    ]


def test_a_failed_claim_does_not_stop_the_run_and_is_retried_within_the_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls: list[str] = []

    def runner(claim_id: str, thread_id: str) -> str:
        calls.append(claim_id)
        if claim_id == "CLM-1001" and calls.count(claim_id) == 1:
            raise RuntimeError("model unavailable")
        decide(client, claim_id, "decline", 0)
        return "done"

    monkeypatch.setattr(main, "run_claim_agent", runner)
    run_id = new_run(1)
    failed = work(client, run_id, "CLM-1001").json()
    assert failed["state"] == "waiting"
    assert failed["message"] == (
        "CLM-1001 failed (The agent stopped with an error: model unavailable)"
        " and is waiting to be tried again."
    )
    retried = work(client, run_id, "CLM-1001").json()
    assert retried["message"] == "CLM-1001 decided: decline."
    assert work(client, run_id, "CLM-1002").status_code == 409


def test_a_claim_that_fails_twice_is_reported_as_left_for_a_handler(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "run_claim_agent", lambda claim_id, thread_id: "done")
    run_id = new_run(5)
    work(client, run_id, "CLM-1001")
    left = work(client, run_id, "CLM-1001").json()
    assert left["state"] == "left_for_handler"
    assert left["message"] == "CLM-1001 failed twice and is left for a handler."


def test_finish_is_refused_while_a_claim_could_still_be_started(
    client: TestClient, declining: None
):
    run_id = new_run(2)
    refused = finish(client, run_id)
    assert refused.status_code == 409
    assert "CLM-1001, CLM-1002" in refused.json()["detail"]
    work(client, run_id, "CLM-1001")
    assert finish(client, run_id).status_code == 409
    work(client, run_id, "CLM-1002")
    done = finish(client, run_id, "Two declined.")
    assert done.status_code == 200
    assert done.json()["state"] == "finished"
    assert done.json()["summary"] == "Two declined."
    assert states(client).count("waiting") == 23
    assert finish(client, run_id).status_code == 409


def test_finish_is_refused_while_a_claim_waits_for_a_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "run_claim_agent", lambda claim_id, thread_id: "done")
    run_id = new_run(1)
    work(client, run_id, "CLM-1001")
    refused = finish(client, run_id)
    assert refused.status_code == 409
    assert "missed CLM-1001." in refused.json()["detail"]
    work(client, run_id, "CLM-1001")
    assert finish(client, run_id).status_code == 200


def test_the_to_do_list_is_replaced_in_order(client: TestClient):
    run_id = new_run(5)
    first = [
        {"text": "CLM-1002", "status": "in_progress"},
        {"text": "CLM-1001", "status": "to_do"},
    ]
    second = [
        {"text": "CLM-1002", "status": "done"},
        {"text": "CLM-1001", "status": "left_for_handler"},
        {"text": "CLM-1003", "status": "to_do"},
    ]
    assert (
        client.put(f"/api/runs/{run_id}/todos", json={"todos": first}).status_code
        == 200
    )
    assert latest(client)["todos"] == first
    client.put(f"/api/runs/{run_id}/todos", json={"todos": second})
    run = latest(client)
    assert run["todos"] == second
    assert (run["limit"], run["state"], run["summary"]) == (5, "running", "")


def test_a_bad_to_do_status_and_a_finished_run_are_refused(client: TestClient):
    run_id = new_run(5)
    bad = {"todos": [{"text": "CLM-1001", "status": "maybe"}]}
    assert client.put(f"/api/runs/{run_id}/todos", json=bad).status_code == 422
    assert client.put("/api/runs/99/todos", json={"todos": []}).status_code == 404
    with closing(connect()) as conn:
        runs.end(conn, run_id, "finished", "Done.")
    assert (
        client.put(f"/api/runs/{run_id}/todos", json={"todos": []}).status_code == 409
    )


def test_startup_fails_a_run_left_going(client: TestClient):
    run_id = new_run(5)
    with TestClient(app) as restarted:
        run = restarted.get("/api/runs/latest").json()
    assert run["run_id"] == run_id
    assert run["state"] == "failed"
    assert run["summary"] == "The backend stopped during this run."
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
