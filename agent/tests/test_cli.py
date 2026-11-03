import sys
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from agent import __main__ as cli
from agent import main
from backend import runs, work
from backend.db import connect
from backend.work import AgentStopped
from conftest import Agents

pytestmark = pytest.mark.usefixtures("in_process_backend")


def decide_on_the_spot(claim_id: str, thread_id: str) -> str:
    body = {
        "decision": "approve" if claim_id == "CLM-1001" else "decline",
        "payout": 420 if claim_id == "CLM-1001" else 0,
        "explanation": "Stub.",
        "policy_section": "3. Trip Delay",
    }
    main.call("POST", f"/api/claims/{claim_id}/decision", body)
    if claim_id == "CLM-1001":
        main.call("POST", f"/api/claims/{claim_id}/payment", {"amount": 420})
    return "done"


def queue_agent_that_works_in_order(run_id: int) -> None:
    """A stand-in queue agent that works the waiting claims, then finishes."""
    for claim in cli.waiting_claims():
        answer = main.call("POST", f"/api/runs/{run_id}/claims/{claim}/work")
        if answer.startswith("Error"):
            break
    main.call("POST", f"/api/runs/{run_id}/finish", {"summary": "Worked them."})


@pytest.fixture(autouse=True)
def stand_ins(agents: Agents) -> None:
    agents.claim(decide_on_the_spot)
    agents.queue(queue_agent_that_works_in_order)


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["agent", *args])
    cli.main()


def test_one_claim(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    run_cli(monkeypatch, "CLM-1001")
    assert capsys.readouterr().out == "CLM-1001: decided, approve 420.00\n"


def test_a_limited_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    run_cli(monkeypatch, "--limit", "2")
    assert capsys.readouterr().out.splitlines() == [
        "CLM-1001: decided, approve 420.00",
        "CLM-1002: decided, decline 0.00",
        "Run finished: Worked them.",
    ]
    states = [c["state"] for c in client.get("/api/claims").json()]
    assert states.count("waiting") == 23


def test_the_whole_queue(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    run_cli(monkeypatch, "CLM-1001")
    capsys.readouterr()
    run_cli(monkeypatch)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 25
    assert lines[0] == "CLM-1002: decided, decline 0.00"
    assert lines[-1] == "Run finished: Worked them."
    assert {c["state"] for c in client.get("/api/claims").json()} == {"decided"}
    run_cli(monkeypatch)
    assert capsys.readouterr().out == "No claims are waiting.\n"


def test_a_claim_that_cannot_start_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    run_cli(monkeypatch, "CLM-1001")
    capsys.readouterr()
    run_cli(monkeypatch, "CLM-1001")
    assert "CLM-1001: Error 409" in capsys.readouterr().out


def test_a_run_that_cannot_start_is_reported(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    with closing(connect()) as conn:
        run_id = runs.create(conn, 1).run_id
    monkeypatch.setattr(work, "working_runs", lambda: frozenset({run_id}))
    run_cli(monkeypatch)
    assert capsys.readouterr().out.startswith("Error 409: A run is already going")


def test_a_run_that_decides_nothing_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agents: Agents,
):
    agents.claim(lambda claim_id, thread_id: "")
    run_cli(monkeypatch, "CLM-1001")
    assert capsys.readouterr().out == "CLM-1001: waiting, no decision\n"


@pytest.mark.parametrize("args", [["CLM-1001", "--limit", "2"], ["--limit"]])
def test_bad_arguments(monkeypatch: pytest.MonkeyPatch, args: list[str]):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, *args)
    assert exit_info.value.code == 2


def test_help(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "--help")
    assert exit_info.value.code == 0
    assert "--limit" in capsys.readouterr().out


def test_a_stopped_claim_and_a_stopped_run_are_carried_on_first(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agents: Agents,
):
    def stopped(claim_id: str, thread_id: str) -> str:
        raise AgentStopped("killed")

    agents.claim(stopped)
    client.post("/api/claims/CLM-1002/run")
    with closing(connect()) as conn:
        run_id = runs.create(conn, 2).run_id
    assert client.get("/api/claims/CLM-1002").json()["state"] == "in_progress"

    agents.claim(decide_on_the_spot)
    run_cli(monkeypatch)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"Carrying on run {run_id}."
    assert "CLM-1002: decided, decline 0.00" in out
    assert client.get("/api/claims/CLM-1002").json()["state"] == "decided"
    assert client.get("/api/runs/latest").json()["run_id"] == run_id


def test_one_stopped_claim_is_carried_on_by_name(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agents: Agents,
):
    def stopped(claim_id: str, thread_id: str) -> str:
        raise AgentStopped("killed")

    agents.claim(stopped)
    client.post("/api/claims/CLM-1002/run")
    agents.claim(decide_on_the_spot)
    run_cli(monkeypatch, "CLM-1002")
    assert capsys.readouterr().out == "CLM-1002: decided, decline 0.00\n"


def test_nothing_to_do_without_waiting_claims_or_a_stopped_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    run_cli(monkeypatch, "--limit", "30")
    capsys.readouterr()
    run_cli(monkeypatch)
    assert capsys.readouterr().out == "No claims are waiting.\n"
