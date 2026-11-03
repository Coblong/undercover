import sys

import pytest
from fastapi.testclient import TestClient

from agent import __main__ as cli
from agent import main
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


@pytest.fixture(autouse=True)
def stand_ins(agents: Agents) -> None:
    agents.claim(decide_on_the_spot)


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["agent", *args])
    cli.main()


def test_one_claim(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    run_cli(monkeypatch, "CLM-1001")
    assert capsys.readouterr().out == "CLM-1001: decided, approve 420.00\n"


def test_a_claim_that_cannot_start_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    run_cli(monkeypatch, "CLM-1001")
    capsys.readouterr()
    run_cli(monkeypatch, "CLM-1001")
    assert "CLM-1001: Error 409" in capsys.readouterr().out


def test_a_run_that_decides_nothing_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agents: Agents,
):
    agents.claim(lambda claim_id, thread_id: "")
    run_cli(monkeypatch, "CLM-1001")
    assert capsys.readouterr().out == "CLM-1001: waiting, no decision\n"


def test_a_claim_id_is_needed_and_the_old_options_are_gone(
    monkeypatch: pytest.MonkeyPatch,
):
    for args in ([], ["--limit", "2"], ["CLM-1001", "--limit", "2"]):
        with pytest.raises(SystemExit) as exit_info:
            run_cli(monkeypatch, *args)
        assert exit_info.value.code == 2


def test_help(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "--help")
    assert exit_info.value.code == 0
    assert "claim_id" in capsys.readouterr().out


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
