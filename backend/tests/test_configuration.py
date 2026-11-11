import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import configurations, db, instructions
from backend.main import app
from backend.tests import signing
from backend.tests.test_rules import decide

HERE = Path(__file__).resolve().parents[1]


def sql(statement: str, *values: Any) -> None:
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        conn.execute(statement, values)


def add_version(version: int, text: str, model: str = "openai/gpt-6-luna") -> None:
    sql(
        "INSERT INTO configurations (version, instructions, model, reasoning_effort,"
        " reason, proposed_by, approved_by, approved_at, created_at)"
        " VALUES (?, ?, ?, 'low', 'Because.', 'Round 1', 'HND-01', 'then', 'then')",
        version,
        text,
        model,
    )


def make_current(version: int) -> None:
    sql("UPDATE current_configuration SET version = ?", version)


def test_seeding_makes_version_1_and_recipe_1(client: TestClient):
    versions = client.get("/api/configurations").json()
    assert len(versions) == 1
    first = versions[0]
    assert (first["version"], first["model"], first["reasoning_effort"]) == (
        1,
        "openai/gpt-6-luna",
        "low",
    )
    assert first["instructions"] == (HERE / "instructions_claim.md").read_text()
    assert first["current"] is True
    assert first["proposed_by"] == "Seed"
    assert first["diff"] == []
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        recipe = conn.execute("SELECT recipe FROM recipes").fetchall()
    assert len(recipe) == 1
    assert recipe[0][0] == (HERE / "recipe_improver.md").read_text()
    assert recipe[0][0].splitlines()[0].startswith("1. Call read_results")


def test_seeding_again_takes_the_configuration_back_to_version_1(client: TestClient):
    add_version(2, "changed")
    make_current(2)
    db.dispose_engines()
    db.reset_db()
    versions = client.get("/api/configurations").json()
    assert [v["version"] for v in versions] == [1]
    assert versions[0]["current"] is True


def test_a_versions_diff_is_against_the_version_before(client: TestClient):
    old = (HERE / "instructions_claim.md").read_text()
    new = old.replace("Never decline a claim for suspected dishonesty.", "Never guess.")
    add_version(2, new)
    versions = client.get("/api/configurations").json()
    assert [v["version"] for v in versions] == [2, 1]
    changes = [line for line in versions[0]["diff"] if line["kind"] != "context"]
    assert [c["kind"] for c in changes] == ["remove", "add"]
    assert "Never decline a claim for suspected dishonesty." in changes[0]["text"]
    assert "Never guess." in changes[1]["text"]
    assert versions[0]["previous_model"] == "openai/gpt-6-luna"
    assert versions[0]["approved_by"] == "Dana Whitfield"
    assert versions[0]["current"] is False


def test_the_diff_of_two_small_texts():
    lines = configurations.diff("a\nb\nc", "a\nB\nc")
    assert [(line.kind, line.text) for line in lines] == [
        ("context", "..."),
        ("context", "a"),
        ("remove", "b"),
        ("add", "B"),
        ("context", "c"),
    ]


def test_the_claim_agents_version_holds_no_scores(client: TestClient):
    current = client.get("/api/configuration/current").json()
    assert set(current) == {"version", "instructions", "model", "reasoning_effort"}
    run = client.post("/api/runs/dispatch", json={"concurrency": 2}).json()
    by_run = client.get(f"/api/runs/{run['run_id']}/configuration").json()
    assert by_run == current
    assert client.get("/api/runs/99/configuration").status_code == 404


def test_the_instructions_are_filled_in_but_the_stored_text_keeps_its_names(
    client: TestClient,
):
    stored = client.get("/api/configurations").json()[0]["instructions"]
    served = client.get("/api/configuration/current").json()["instructions"]
    assert "{approval_limit}" in stored
    assert "{approval_limit}" not in served
    assert instructions.render("{x} and {approval_limit}", 750) == "{x} and $750"


def test_a_dispatch_and_an_evaluation_record_the_current_version(client: TestClient):
    first = client.post("/api/runs/dispatch", json={"concurrency": 2}).json()
    assert first["config_version"] == 1
    client.post("/api/runs/stop")
    add_version(2, "new text", model="google/gemini-3.5-flash-lite")
    make_current(2)
    second = client.post("/api/runs/dispatch", json={"concurrency": 2}).json()
    assert (second["config_version"], second["model"]) == (
        2,
        "google/gemini-3.5-flash-lite",
    )
    client.post("/api/runs/stop")
    evaluation = client.post(
        "/api/runs/evaluation",
        json={"concurrency": 2, "eval_set": "evaluation", "tiny": True},
    ).json()
    assert evaluation["config_version"] == 2
    served = client.get(f"/api/runs/{evaluation['run_id']}/configuration").json()
    assert served["instructions"] == "new text"


def test_free_model_seeding_uses_the_model_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "free.db"))
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    with TestClient(app) as client:
        first = client.get("/api/configurations").json()[0]
        assert (first["model"], first["reasoning_effort"]) == (
            "openrouter/free",
            "default",
        )
        models = client.get("/api/settings").json()["models"]
        assert models == [
            {
                "model": "openrouter/free",
                "reasoning_efforts": ["default"],
                "input_price": 0,
                "output_price": 0,
            }
        ]


def test_signing_helpers_are_in_use():
    assert signing.DANA.endswith("@undercover.example")


def test_the_approval_limit_comes_from_the_settings(client: TestClient):
    values = client.get("/api/settings").json()["values"]
    decide(client, "CLM-1002", "approve", 800)
    assert (
        client.get("/api/claims/CLM-1002").json()["decision"]["approval_limit"] == 1000
    )
    client.put("/api/settings", json={**values, "approval_limit": 500})
    served = client.get("/api/configuration/current").json()["instructions"]
    assert "A payout over $500 needs" in served
    # A decision recorded before the change keeps the limit it had.
    paid = client.post("/api/claims/CLM-1002/payment", json={"amount": 800})
    assert paid.status_code == 200
    # The next decision uses the new limit.
    second = decide(client, "CLM-1003", "approve", 800)
    assert second.json()["state"] == "waiting_for_sign_off"
    claim = client.get("/api/claims/CLM-1003").json()
    assert (claim["needs_sign_off"], claim["decision"]["approval_limit"]) == (True, 500)
    blocked = client.post("/api/claims/CLM-1003/payment", json={"amount": 800})
    assert blocked.status_code == 409
    assert "$800.00 is over the $500.00 limit" in blocked.json()["detail"]
