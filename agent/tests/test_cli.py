import sys

import pytest
from fastapi.testclient import TestClient

from agent import __main__ as cli
from agent import main
from backend import main as backend_main
from backend.main import app


@pytest.fixture(autouse=True)
def in_process_backend(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))

    def decide_on_the_spot(claim_id: str, thread_id: str) -> str:
        body = {
            "decision": "approve" if claim_id == "CLM-1001" else "decline",
            "payout": 420 if claim_id == "CLM-1001" else 0,
            "explanation": "Stub.",
            "policy_section": "3. Trip Delay",
        }
        main.call("POST", f"/api/claims/{claim_id}/decision", body)
        return "done"

    monkeypatch.setattr(backend_main, "run_claim_agent", decide_on_the_spot)


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["agent", *args])
    cli.main()


def test_one_claim(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    run_cli(monkeypatch, "CLM-1001")
    assert capsys.readouterr().out == "CLM-1001: decided, approve 420.00\n"


def test_all_waiting_claims(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    run_cli(monkeypatch, "CLM-1001")
    capsys.readouterr()
    run_cli(monkeypatch, "--all")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 24
    assert lines[0] == "CLM-1002: decided, decline 0.00"
    assert {c["state"] for c in client.get("/api/claims").json()} == {"decided"}


def test_a_claim_that_cannot_start_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    run_cli(monkeypatch, "CLM-1001")
    capsys.readouterr()
    run_cli(monkeypatch, "CLM-1001")
    assert "CLM-1001: Error 409" in capsys.readouterr().out


def test_a_run_that_decides_nothing_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(backend_main, "run_claim_agent", lambda claim_id, thread_id: "")
    run_cli(monkeypatch, "CLM-1001")
    assert capsys.readouterr().out == "CLM-1001: waiting, no decision\n"


def test_needs_a_claim_or_all(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch)
    assert exit_info.value.code == 2
