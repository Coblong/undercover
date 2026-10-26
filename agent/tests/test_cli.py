import sys

import pytest
from fastapi.testclient import TestClient

from agent import __main__ as cli
from agent import main
from backend import runs
from backend.db import connect
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
    runs.create(connect(), 1)
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
