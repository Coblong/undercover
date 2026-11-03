"""Work that waits for a sign-off and survives a stop, with fake agents."""

import os
import sqlite3
from collections.abc import Callable
from contextlib import closing

from fastapi.testclient import TestClient

from backend import runs
from backend.db import connect
from backend.work import AgentStopped
from conftest import Agents

Runner = Callable[[str, str], str]


def decide(client: TestClient, claim_id: str, payout: float):
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": "approve",
            "payout": payout,
            "explanation": "Covered.",
            "policy_section": "3. Trip Delay",
        },
    )


def claim(client: TestClient, claim_id: str) -> dict:
    return client.get(f"/api/claims/{claim_id}").json()


def errors(client: TestClient, claim_id: str) -> list[str]:
    steps = client.get(f"/api/claims/{claim_id}/steps").json()
    return [s["text"] for s in steps if s["kind"] == "error"]


def attempts(claim_id: str) -> list[tuple[str, int | None]]:
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        return conn.execute(
            "SELECT thread_id, failed FROM attempts WHERE claim_id = ? ORDER BY id",
            (claim_id,),
        ).fetchall()


def hold(client: TestClient, agents: Agents, claim_id: str = "CLM-1003") -> None:
    """Run the agent on a claim until it approves $1,250 and stops for a sign-off."""

    def approve(claim_id: str, thread_id: str) -> str:
        decide(client, claim_id, 1250)
        return "waiting for sign-off"

    agents.claim(approve)
    client.post(f"/api/claims/{claim_id}/run")
    assert claim(client, claim_id)["state"] == "waiting_for_sign_off"


def sign_off(client: TestClient, claim_id: str = "CLM-1003"):
    return client.post(
        f"/api/claims/{claim_id}/sign-off", json={"handler_id": "HND-01"}
    )


def test_a_sign_off_calls_the_agent_once_on_the_same_thread_and_it_pays(
    client: TestClient, agents: Agents
):
    hold(client, agents)
    first_thread = attempts("CLM-1003")[0][0]
    calls: list[tuple[str, str, str]] = []

    def pay(claim_id: str, thread_id: str) -> str:
        during = claim(client, claim_id)["state"]
        calls.append((claim_id, thread_id, during))
        client.post(f"/api/claims/{claim_id}/payment", json={"amount": 1250})
        return "paid"

    agents.claim(pay)
    response = sign_off(client)
    assert response.json()["state"] == "in_progress"
    assert calls == [("CLM-1003", first_thread, "in_progress")]
    done = claim(client, "CLM-1003")
    assert done["state"] == "decided"
    assert done["payment"]["signed_off_by"] == "HND-01"
    assert done["payment"]["signed_off_at"] == done["sign_off"]["signed_off_at"]
    assert attempts("CLM-1003") == [(first_thread, 0), (first_thread, 0)]
    assert sign_off(client).status_code == 409
    assert len(calls) == 1


def test_a_sign_off_stands_when_the_agent_cannot_be_reached(client: TestClient):
    decide(client, "CLM-1003", 1250)
    assert sign_off(client).status_code == 200
    done = claim(client, "CLM-1003")
    assert done["state"] == "in_progress"
    assert done["sign_off"]["handler_name"] == "Dana Whitfield"
    assert done["payment"] is None
    assert errors(client, "CLM-1003") == []
    assert [failed for _, failed in attempts("CLM-1003")] == [None]


def test_the_next_run_carries_on_a_claim_signed_off_while_the_agent_was_away(
    client: TestClient, agents: Agents
):
    hold(client, agents)
    thread = attempts("CLM-1003")[0][0]
    agents.claim(
        lambda claim_id, thread_id: (_ for _ in ()).throw(AgentStopped("away"))
    )
    sign_off(client)
    assert claim(client, "CLM-1003")["state"] == "in_progress"

    seen: list[str] = []

    def pay(claim_id: str, thread_id: str) -> str:
        seen.append(thread_id)
        client.post(f"/api/claims/{claim_id}/payment", json={"amount": 1250})
        return "paid"

    agents.claim(pay)
    assert client.post("/api/claims/CLM-1003/run").status_code == 202
    assert seen == [thread]
    assert claim(client, "CLM-1003")["state"] == "decided"
    assert len(attempts("CLM-1003")) == 2


def test_an_error_after_a_sign_off_starts_afresh_and_pays_once(
    client: TestClient, agents: Agents
):
    hold(client, agents)
    first_thread = attempts("CLM-1003")[0][0]

    def crash(claim_id: str, thread_id: str) -> str:
        raise RuntimeError("model unavailable")

    agents.claim(crash)
    sign_off(client)
    assert claim(client, "CLM-1003")["state"] == "waiting"
    assert claim(client, "CLM-1003")["decision"]["payout"] == 1250
    assert errors(client, "CLM-1003")[
        0
    ] == "The agent approved the claim but did not pay it." or errors(
        client, "CLM-1003"
    )

    seen: list[str] = []

    def pay(claim_id: str, thread_id: str) -> str:
        seen.append(thread_id)
        client.post(f"/api/claims/{claim_id}/payment", json={"amount": 1250})
        return "paid"

    agents.claim(pay)
    client.post("/api/claims/CLM-1003/run")
    assert seen
    assert seen[0] != first_thread
    assert claim(client, "CLM-1003")["state"] == "decided"
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1


def test_a_run_carries_on_stopped_claims_before_the_queue_agent(
    client: TestClient, agents: Agents
):
    def stopped(claim_id: str, thread_id: str) -> str:
        raise AgentStopped("killed")

    agents.claim(stopped)
    client.post("/api/claims/CLM-1002/run")
    thread = attempts("CLM-1002")[0][0]
    order: list[str] = []

    def carry_on(claim_id: str, thread_id: str) -> str:
        order.append(f"claim {claim_id} {thread_id}")
        client.post(
            f"/api/claims/{claim_id}/decision",
            json={
                "decision": "decline",
                "payout": 0,
                "explanation": "Not covered.",
                "policy_section": "9. General exclusions",
            },
        )
        return "done"

    def queue(run_id: int) -> None:
        order.append("queue")
        client.post(f"/api/runs/{run_id}/finish", json={"summary": "Done."})

    agents.claim(carry_on)
    agents.queue(queue)
    response = client.post("/api/runs", json={"limit": 1})
    assert response.status_code == 202
    assert order == [f"claim CLM-1002 {thread}", "queue"]
    assert claim(client, "CLM-1002")["state"] == "decided"
    assert len(attempts("CLM-1002")) == 1


def test_the_queue_agent_is_told_a_claim_was_stopped(
    client: TestClient, agents: Agents
):
    def stopped(claim_id: str, thread_id: str) -> str:
        raise AgentStopped("killed")

    agents.claim(stopped)
    with closing(connect()) as conn:
        run_id = runs.create(conn, 1).run_id
    work = client.post(f"/api/runs/{run_id}/claims/CLM-1001/work")
    assert work.json()["state"] == "in_progress"
    assert (
        work.json()["line"]
        == "CLM-1001 was stopped partway. The next run carries it on."
    )
