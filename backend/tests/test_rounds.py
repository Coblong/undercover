import os
import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import db, improvement, judge
from backend.tests import signing

SECTION = "3. Trip Delay"
BASE = "/api/improvement/rounds"


def new_round(client: TestClient) -> dict[str, Any]:
    response = client.post(BASE, json={"handler_id": "HND-01"})
    assert response.status_code == 201, response.text
    return response.json()


def get_round(client: TestClient, round_id: int = 1) -> dict[str, Any]:
    return client.get(f"{BASE}/{round_id}").json()


def change(**changes: Any) -> dict[str, Any]:
    return {
        "kind": "change",
        "instructions": "Decide the claim.\nNever approve what the wording declines.",
        "model": "openai/gpt-6-luna",
        "reasoning_effort": "low",
        "recipe": None,
        "reason": "Approvals of declined claims need a rule.",
        **changes,
    }


def recipe() -> dict[str, Any]:
    return {
        "kind": "recipe",
        "instructions": None,
        "model": None,
        "reasoning_effort": None,
        "recipe": "1. Read the results.\n2. Propose a change.",
        "reason": "A shorter recipe.",
    }


def decision(outcome: str, reason: str | None = None) -> dict[str, Any]:
    return {"outcome": outcome, "handler_id": "HND-01", "reason": reason}


def approve_change(client: TestClient, round_id: int = 1) -> None:
    proposed = client.post(f"{BASE}/{round_id}/proposal", json=change())
    assert proposed.status_code == 200, proposed.text
    approved = client.post(f"{BASE}/{round_id}/decision", json=decision("approved"))
    assert approved.status_code == 200, approved.text


def versions(client: TestClient) -> list[dict[str, Any]]:
    return client.get("/api/configurations").json()


def rows(statement: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        return conn.execute(statement).fetchall()


def answer_key(eval_set: str) -> dict[str, tuple[str, float]]:
    found = rows(
        "SELECT claim_id, decision, payout FROM answer_key"  # noqa: S608
        f" WHERE eval_set = '{eval_set}'"
    )
    return {claim: (decision, payout) for claim, decision, payout in found}


@pytest.fixture(autouse=True)
def stub_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grade an explanation by the number it carries: "grade=1.5", or "grade=none"."""

    def grade(case: judge.Case) -> judge.Grade:
        text = case.agent_reason.removeprefix("grade=")
        if text == "none":
            raise TimeoutError("too slow")
        return judge.Grade(float(text), "B", True, True, 0.0001)

    monkeypatch.setattr(judge, "grade", grade)


def play(
    client: TestClient,
    round_id: int | None,
    eval_set: str,
    right: int,
    **options: Any,
) -> int:
    """Run a whole-set evaluation through the API, with `right` claims right."""
    grade = options.get("grade", "2.0")
    cost = options.get("cost", 0.001)
    seconds = options.get("seconds", 10.0)
    grades = options.get("grades") or {}
    created = client.post(
        "/api/runs/evaluation",
        json={
            "concurrency": 25,
            "eval_set": eval_set,
            "tiny": False,
            "round_id": round_id,
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    key = answer_key(eval_set)
    claims = client.get(f"/api/evaluations/{run_id}").json()["results"]
    for position, claim in enumerate(claims):
        copy, source = claim["claim_id"], claim["source_id"]
        started = client.post(f"/api/runs/{run_id}/claims/{copy}/start")
        assert started.status_code == 200, started.text
        handler, payout = key[source]
        if position >= right:
            handler, payout = (
                ("decline", 0.0) if handler != "decline" else ("refer", 0.0)
            )
        body = {
            "decision": handler,
            "payout": payout,
            "explanation": grades.get(source, f"grade={grade}"),
            "policy_section": SECTION,
        }
        client.post(f"/api/claims/{copy}/decision", json=body)
        client.post(
            f"/api/claims/{copy}/model-calls",
            json={
                "run_id": run_id,
                "input_tokens": 1,
                "output_tokens": 1,
                "cost": cost,
            },
        )
        client.post(
            f"/api/claims/{copy}/work-times",
            json={"run_id": run_id, "seconds": seconds},
        )
        client.post(f"/api/claims/{copy}/attempt/end", json={"error": None})
    client.post(f"/api/runs/{run_id}/end", json={"state": "finished"})
    return run_id


def baseline(client: TestClient, round_id: int = 1, eval_right: int = 16) -> None:
    play(client, round_id, "evaluation", eval_right)
    play(client, round_id, "held_out", 16)
    play(client, round_id, "held_out", 16)


# --- proposals ---------------------------------------------------------------


def test_a_proposal_changes_nothing_until_a_handler_approves_it(client: TestClient):
    new_round(client)
    proposed = client.post(f"{BASE}/1/proposal", json=change()).json()
    assert proposed["stage"] == "change_waiting"
    assert proposed["pending"]["kind"] == "change"
    assert proposed["pending"]["version"] == 2
    assert len(versions(client)) == 1
    assert client.get("/api/configuration/current").json()["version"] == 1

    approved = client.post(f"{BASE}/1/decision", json=decision("approved")).json()
    assert approved["pending"] is None
    assert approved["tried_version"] == 2
    two, one = versions(client)
    assert (two["version"], one["version"]) == (2, 1)
    assert two["proposed_by"] == "Improver, round 1"
    assert two["approved_by"] == "Dana Whitfield"
    assert two["approved_at"]
    assert two["reason"] == "Approvals of declined claims need a rule."
    # A kept version is made current by the verdict, not by approval.
    assert two["current"] is False
    assert approved["decisions"][0]["handler"] == "Dana Whitfield"


def test_the_pending_proposal_shows_its_diff_and_the_model_before(client: TestClient):
    new_round(client)
    pending = client.post(
        f"{BASE}/1/proposal", json=change(model="deepseek/deepseek-v4.1-flash")
    ).json()["pending"]
    assert (pending["previous_model"], pending["model"]) == (
        "openai/gpt-6-luna",
        "deepseek/deepseek-v4.1-flash",
    )
    assert any(line["kind"] == "add" for line in pending["diff"])
    assert any(line["kind"] == "remove" for line in pending["diff"])


def test_a_rejection_records_the_handler_and_the_reason_and_makes_no_version(
    client: TestClient,
):
    new_round(client)
    client.post(f"{BASE}/1/proposal", json=change())
    rejected = client.post(
        f"{BASE}/1/decision", json=decision("rejected", "Too specific.")
    ).json()
    assert rejected["pending"] is None
    assert rejected["tried_version"] is None
    assert rejected["decisions"][0]["outcome"] == "rejected"
    assert rejected["decisions"][0]["reason"] == "Too specific."
    assert len(versions(client)) == 1
    # The improver may propose again.
    assert client.post(f"{BASE}/1/proposal", json=change()).status_code == 200


def test_a_proposal_outside_the_models_allowed_is_refused(client: TestClient):
    new_round(client)
    for bad in (
        change(model="openai/gpt-9"),
        change(reasoning_effort="high"),
        change(model="deepseek/deepseek-v4.1-flash", reasoning_effort="medium"),
    ):
        refused = client.post(f"{BASE}/1/proposal", json=bad)
        assert refused.status_code == 422
        assert "is not one of the models allowed" in refused.json()["detail"]
    assert get_round(client)["pending"] is None


def test_a_proposal_needs_its_parts_and_only_one_waits_at_a_time(client: TestClient):
    new_round(client)
    assert (
        client.post(f"{BASE}/1/proposal", json=change(instructions=None)).status_code
        == 422
    )
    assert (
        client.post(f"{BASE}/1/proposal", json={**recipe(), "recipe": None}).status_code
        == 422
    )
    client.post(f"{BASE}/1/proposal", json=change())
    assert client.post(f"{BASE}/1/proposal", json=change()).status_code == 409
    assert (
        client.post(f"{BASE}/1/decision", json=decision("approved")).status_code == 200
    )
    # A round tries one version.
    assert client.post(f"{BASE}/1/proposal", json=change()).status_code == 409
    assert (
        client.post(f"{BASE}/1/decision", json=decision("approved")).status_code == 409
    )


def test_a_recipe_is_approved_into_a_new_version_of_the_recipe(client: TestClient):
    new_round(client)
    client.post(f"{BASE}/1/proposal", json=recipe())
    assert get_round(client)["stage"] == "recipe_waiting"
    client.post(f"{BASE}/1/decision", json=decision("approved"))
    found = rows(
        "SELECT version, proposed_by, approved_by FROM recipes ORDER BY version"
    )
    assert found == [(1, "Seed", None), (2, "Improver, round 1", "HND-01")]


def test_only_one_round_is_open_at_a_time(client: TestClient):
    new_round(client)
    again = client.post(BASE, json={"handler_id": "HND-01"})
    assert again.status_code == 409
    assert "already open" in again.json()["detail"]
    client.post(f"{BASE}/1/end", json={"state": "finished", "why": None})
    assert client.post(BASE, json={"handler_id": "HND-01"}).status_code == 201


# --- scores and the first evaluations ---------------------------------------------


def test_a_round_starts_by_needing_one_evaluation_and_two_held_out_runs(
    client: TestClient,
):
    new_round(client)
    assert get_round(client)["needs"] == {"evaluation": 1, "held_out": 2}
    play(client, 1, "evaluation", 20)
    play(client, 1, "held_out", 20)
    assert get_round(client)["needs"] == {"evaluation": 0, "held_out": 1}
    play(client, 1, "held_out", 22)
    assert get_round(client)["needs"] == {"evaluation": 0, "held_out": 0}


def test_a_versions_scores_count_only_the_whole_set_runs_its_rounds_made(
    client: TestClient,
):
    new_round(client)
    play(client, None, "held_out", 25)  # a Scoreboard run: no round
    tiny = client.post(
        "/api/runs/evaluation",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": True, "round_id": 1},
    ).json()["run_id"]
    client.post(f"/api/runs/{tiny}/end", json={"state": "finished"})
    play(client, 1, "held_out", 20, cost=0.002, seconds=10)
    play(client, 1, "held_out", 22, cost=0.004, seconds=20, grade="1.0")
    stopped = client.post(
        "/api/runs/evaluation",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": False, "round_id": 1},
    ).json()["run_id"]
    client.post("/api/runs/stop")
    assert client.get(f"/api/evaluations/{stopped}").json()["state"] == "stopped"
    with db.connect() as conn:
        scores = improvement.version_scores(conn, 1, "held_out")
        none = improvement.version_scores(conn, 1, "evaluation")
    assert none is None
    assert scores is not None
    assert scores.runs == 2
    assert scores.accuracy == pytest.approx((20 / 25 + 22 / 25) / 2)
    assert scores.grade == pytest.approx((2.0 + 1.0) / 2)
    assert scores.cost_per_claim == pytest.approx(0.003)
    assert scores.seconds_per_claim == pytest.approx(15.0)


# --- the improver's view ------------------------------------------------------------


def test_the_improvers_view_holds_nothing_from_the_held_out_set_but_its_scores(
    client: TestClient,
):
    new_round(client)
    key = answer_key("evaluation")
    sources = list(key)
    grades = {
        source: f"grade={0.2 * position + 0.1:.1f}"
        for position, source in enumerate(sources)
    }
    play(client, 1, "evaluation", 20, grades={s: g for s, g in grades.items()})
    play(client, 1, "held_out", 20)
    play(client, 1, "held_out", 22)
    view = client.get(f"{BASE}/1/view").json()["report"]

    assert view["current_version"]["version"] == 1
    assert view["scores"]["held_out"]["runs"] == 2
    assert view["scores"]["evaluation"]["runs"] == 1
    mistakes = view["evaluation_set"]
    assert len(mistakes["wrong"]) == 5
    assert len(mistakes["explained_least_well"]) == 5
    assert all(not c["right"] for c in mistakes["wrong"])
    assert all(c["right"] for c in mistakes["explained_least_well"])
    least = [c["judge"]["grade"] for c in mistakes["explained_least_well"]]
    assert least == sorted(least)
    first = mistakes["wrong"][0]
    assert first["handler"]["reason"]
    assert first["agent"]["reason"].startswith("grade=")
    assert set(first["judge"]) == {"grade", "letter", "same_grounds", "same_section"}

    held_out = answer_key("held_out")
    text = str(view)
    for claim_id in held_out:
        assert claim_id not in text
    reasons = rows("SELECT reason FROM answer_key WHERE eval_set = 'held_out'")
    summaries = rows("SELECT summary FROM held_out_claims")
    for (held,) in reasons + summaries:
        assert held not in text
    assert view["spending_left"] == pytest.approx(
        0.5 - get_round(client)["cost"], abs=1e-4
    )
    assert [m["model"] for m in view["models_allowed"]][0] == "openai/gpt-6-luna"
    assert view["objectives_on_the_held_out_set"] == {"accuracy": 0.6, "grade": 1.0}


# --- the verdict -------------------------------------------------------------------


def test_a_version_is_kept_when_the_rule_says_so_and_becomes_current(
    client: TestClient,
):
    new_round(client)
    baseline(client)
    approve_change(client)
    play(client, 1, "evaluation", 20)
    play(client, 1, "held_out", 22)
    play(client, 1, "held_out", 22)
    done = client.post(f"{BASE}/1/verdict", json={"reason": None}).json()
    # Held-out accuracy 16/25 = 0.64 before and 22/25 = 0.88 after: 0.24 better.
    assert done["verdict"] == "kept"
    assert done["why"] == "accuracy improved"
    assert done["held_out_before"]["accuracy"] == pytest.approx(0.64)
    assert done["held_out_after"]["accuracy"] == pytest.approx(0.88)
    assert done["held_out_before"]["runs"] == done["held_out_after"]["runs"] == 2
    assert client.get("/api/configuration/current").json()["version"] == 2
    assert client.post(f"{BASE}/1/verdict", json={"reason": None}).status_code == 409


def test_a_version_is_rolled_back_when_nothing_improves(client: TestClient):
    new_round(client)
    baseline(client)
    approve_change(client)
    play(client, 1, "evaluation", 16)
    play(client, 1, "held_out", 16)
    play(client, 1, "held_out", 16)
    done = client.post(f"{BASE}/1/verdict", json={"reason": None}).json()
    assert done["verdict"] == "rolled_back"
    assert done["why"] == "no measure improved by more than the noise"
    assert client.get("/api/configuration/current").json()["version"] == 1


def test_a_version_with_no_grade_is_rolled_back(client: TestClient):
    new_round(client)
    baseline(client)
    approve_change(client)
    play(client, 1, "evaluation", 20, grade="none")
    play(client, 1, "held_out", 24, grade="none")
    play(client, 1, "held_out", 24, grade="none")
    done = client.post(f"{BASE}/1/verdict", json={"reason": None}).json()
    assert (done["verdict"], done["why"]) == ("rolled_back", "it has no grade")
    assert client.get("/api/configuration/current").json()["version"] == 1


def test_a_version_that_missed_the_objectives_or_got_worse_is_rolled_back(
    client: TestClient,
):
    new_round(client)
    baseline(client)
    approve_change(client)
    play(client, 1, "evaluation", 20)
    play(client, 1, "held_out", 24, grade="0.5")
    play(client, 1, "held_out", 24, grade="0.5")
    done = client.post(f"{BASE}/1/verdict", json={"reason": None}).json()
    assert (done["verdict"], done["why"]) == (
        "rolled_back",
        "it missed the objectives on grade",
    )


def test_a_version_whose_evaluations_did_not_finish_is_rolled_back(client: TestClient):
    new_round(client)
    baseline(client)
    approve_change(client)
    play(client, 1, "held_out", 24)
    done = client.post(f"{BASE}/1/verdict", json={"reason": None}).json()
    assert (done["verdict"], done["why"]) == (
        "rolled_back",
        "its evaluations did not all finish",
    )


def test_a_round_cannot_judge_a_version_it_has_not_tried(client: TestClient):
    new_round(client)
    assert client.post(f"{BASE}/1/verdict", json={"reason": None}).status_code == 409


def test_a_version_approved_but_never_evaluated_is_rolled_back_when_the_round_ends(
    client: TestClient,
):
    new_round(client)
    approve_change(client)
    ended = client.post(f"{BASE}/1/end", json={"state": "finished", "why": None}).json()
    assert (ended["verdict"], ended["why"]) == ("rolled_back", "it was never evaluated")
    assert client.get("/api/configuration/current").json()["version"] == 1


def test_a_round_that_proposes_nothing_finishes_with_no_version_tried(
    client: TestClient,
):
    new_round(client)
    ended = client.post(f"{BASE}/1/end", json={"state": "finished", "why": None}).json()
    assert (ended["state"], ended["tried_version"], ended["verdict"]) == (
        "finished",
        None,
        None,
    )


def test_evaluations_in_a_round_work_the_version_under_test(client: TestClient):
    new_round(client)
    first = client.post(
        "/api/runs/evaluation",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": True, "round_id": 1},
    ).json()
    assert first["config_version"] == 1
    client.post(f"/api/runs/{first['run_id']}/end", json={"state": "finished"})
    approve_change(client)
    second = client.post(
        "/api/runs/evaluation",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": True, "round_id": 1},
    ).json()
    assert second["config_version"] == 2
    served = client.get(f"/api/runs/{second['run_id']}/configuration").json()
    assert served["version"] == 2


# --- settings, stopping and restarting ---------------------------------------


def test_a_round_keeps_the_settings_it_started_with(client: TestClient):
    new_round(client)
    values = client.get("/api/settings").json()["values"]
    client.put(
        "/api/settings", json={**values, "spending_limit": 0.1, "agents_at_once": 3}
    )
    kept = get_round(client)["settings"]
    assert (kept["spending_limit"], kept["agents_at_once"]) == (0.5, 25)
    client.post(f"{BASE}/1/end", json={"state": "finished", "why": None})
    assert new_round(client)["settings"]["spending_limit"] == 0.1


def test_a_stopped_round_leaves_the_current_version_and_records_nothing_more(
    client: TestClient,
):
    new_round(client)
    approve_change(client)
    run = client.post(
        "/api/runs/evaluation",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": True, "round_id": 1},
    ).json()["run_id"]
    stopped = client.post("/api/rounds/1/stop").json()
    assert (stopped["state"], stopped["stage"], stopped["stopped_by"]) == (
        "stopped",
        "stopped",
        "Dana Whitfield",
    )
    assert client.get("/api/configuration/current").json()["version"] == 1
    refused = [
        client.post(
            f"{BASE}/1/stage", json={"stage": "evaluating", "done": 1, "of": 3}
        ),
        client.post(f"{BASE}/1/proposal", json=recipe()),
        client.post(
            f"{BASE}/1/calls", json={"input_tokens": 1, "output_tokens": 1, "cost": 0.1}
        ),
        client.post(f"{BASE}/1/verdict", json={"reason": None}),
        client.post(f"{BASE}/1/end", json={"state": "finished", "why": None}),
        client.post("/api/rounds/1/stop"),
        client.post(
            "/api/runs/evaluation",
            json={
                "concurrency": 5,
                "eval_set": "held_out",
                "tiny": True,
                "round_id": 1,
            },
        ),
        client.post(f"/api/runs/{run}/claims/EV{run}-CLM-1104/start"),
    ]
    assert {r.status_code for r in refused} == {409}
    assert refused[0].json()["detail"] == "Round 1 was stopped"
    assert "was stopped" in refused[-1].json()["detail"]


def test_a_round_stopped_after_its_verdict_keeps_the_version(client: TestClient):
    new_round(client)
    baseline(client)
    approve_change(client)
    play(client, 1, "evaluation", 20)
    play(client, 1, "held_out", 22)
    play(client, 1, "held_out", 22)
    client.post(f"{BASE}/1/verdict", json={"reason": None})
    client.post("/api/rounds/1/stop")
    assert client.get("/api/configuration/current").json()["version"] == 2
    assert get_round(client)["verdict"] == "kept"


def test_a_service_start_stops_a_round_in_mid_work_and_leaves_a_waiting_one(
    client: TestClient,
):
    new_round(client)
    assert client.post("/api/runs/stop").json()["stopped_rounds"] == 1
    assert get_round(client)["state"] == "stopped"
    new_round(client)
    client.post(f"{BASE}/2/proposal", json=change())
    assert client.post("/api/runs/stop").json()["stopped_rounds"] == 0
    assert get_round(client, 2)["state"] == "open"
    assert get_round(client, 2)["pending"] is not None


# --- the spending limit ---------------------------------------------------------------


def test_the_estimate_is_the_claims_times_the_highest_cost_per_claim_seen(
    client: TestClient,
):
    new_round(client)
    play(client, 1, "evaluation", 20, cost=0.004)
    with db.connect() as conn:
        assert improvement.estimate(conn, "openai/gpt-6-luna", 25) == pytest.approx(0.1)
        # A model with no runs: Luna's cost scaled by the larger price ratio.
        # Gemini Flash Lite costs 3 times as much in and 5 times as much out,
        # and DeepSeek 3 times in and 2.4 times out.
        assert improvement.estimate(
            conn, "google/gemini-3.5-flash-lite", 25
        ) == pytest.approx(0.1 * 5)
        assert improvement.estimate(
            conn, "deepseek/deepseek-v4.1-flash", 25
        ) == pytest.approx(0.1 * 3)


def test_with_nothing_seen_there_is_nothing_to_estimate_from(client: TestClient):
    new_round(client)
    with db.connect() as conn:
        assert improvement.estimate(conn, "openai/gpt-6-luna", 25) == 0.0
    check = client.post(f"{BASE}/1/check", json={"eval_set": "held_out"}).json()
    assert check["allowed"] is True


def test_an_evaluation_that_would_go_over_the_limit_is_refused(client: TestClient):
    values = client.get("/api/settings").json()["values"]
    client.put("/api/settings", json={**values, "spending_limit": 0.3})
    new_round(client)
    play(client, 1, "evaluation", 20, cost=0.004)  # 25 claims: $0.10 so far
    allowed = client.post(f"{BASE}/1/check", json={"eval_set": "held_out"}).json()
    assert allowed["allowed"] is True
    assert allowed["estimate"] == pytest.approx(0.1)
    play(client, 1, "held_out", 20, cost=0.004)
    client.post(  # the improver's own calls take the rest
        f"{BASE}/1/calls", json={"input_tokens": 1, "output_tokens": 1, "cost": 0.1}
    )
    refused = client.post(f"{BASE}/1/check", json={"eval_set": "held_out"}).json()
    assert refused["allowed"] is False
    assert refused["left"] == pytest.approx(0, abs=1e-6)
    assert refused["message"].startswith("Refused:")


def test_the_spend_counts_the_improver_the_evaluations_and_the_judge(
    client: TestClient,
):
    new_round(client)
    client.post(
        f"{BASE}/1/calls", json={"input_tokens": 5, "output_tokens": 5, "cost": 0.02}
    )
    play(client, 1, "evaluation", 20, cost=0.001)
    snapshot = get_round(client)
    # 25 claims at $0.001, 25 judge calls at $0.0001, and the improver's $0.02.
    assert snapshot["improver_cost"] == pytest.approx(0.02)
    assert snapshot["cost"] == pytest.approx(0.02 + 0.025 + 0.0025)
    assert snapshot["spending_left"] == pytest.approx(0.5 - snapshot["cost"])


def test_once_a_round_has_spent_its_limit_no_new_claim_starts_and_the_run_is_stopped(
    client: TestClient,
):
    values = client.get("/api/settings").json()["values"]
    client.put("/api/settings", json={**values, "spending_limit": 0.05})
    new_round(client)
    approve_change(client)
    run = client.post(
        "/api/runs/evaluation",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": True, "round_id": 1},
    ).json()["run_id"]
    sources = ["CLM-1104", "CLM-1106", "CLM-1110", "CLM-1112", "CLM-1114"]
    first = f"EV{run}-{sources[0]}"
    assert client.post(f"/api/runs/{run}/claims/{first}/start").status_code == 200
    client.post(
        f"/api/claims/{first}/model-calls",
        json={"run_id": run, "input_tokens": 1, "output_tokens": 1, "cost": 0.06},
    )
    second = f"EV{run}-{sources[1]}"
    refused = client.post(f"/api/runs/{run}/claims/{second}/start")
    assert refused.status_code == 409
    assert "spent its limit of $0.05" in refused.json()["detail"]
    client.post(f"/api/claims/{first}/attempt/end", json={"error": None})
    client.post(f"/api/runs/{run}/end", json={"state": "finished"})
    assert client.get(f"/api/evaluations/{run}").json()["state"] == "stopped"
    done = client.post(
        f"{BASE}/1/verdict",
        json={"reason": "the round could not afford to evaluate it"},
    ).json()
    assert done["verdict"] == "rolled_back"
    assert done["why"] == "the round could not afford to evaluate it"
    assert client.get("/api/configuration/current").json()["version"] == 1


# --- who may do what ------------------------------------------------------------------


@pytest.fixture
def started(agents) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def starter(handler_id: str, name: str) -> int:
        calls.append((handler_id, name))
        return 1

    agents.round_start(starter)
    return calls


def test_a_senior_handler_starts_a_round_through_the_service(
    client: TestClient, started: list[tuple[str, str]]
):
    new_round(client)
    response = client.post("/api/rounds")
    assert response.status_code == 202
    assert started == [("HND-01", "Dana Whitfield")]
    assert response.json()["id"] == 1


@pytest.mark.parametrize("email", [signing.MARCUS, signing.PRIYA])
def test_other_handlers_cannot_start_stop_approve_or_reject(
    client: TestClient, agents, started, email: str
):
    resumed: list[tuple[int, str]] = []
    agents.round_resume(
        lambda rid, outcome, hid, reason: resumed.append((rid, outcome))
    )
    new_round(client)
    client.post(f"{BASE}/1/proposal", json=change())
    signing.sign_in_as(client, email)
    refused = [
        client.post("/api/rounds"),
        client.post("/api/rounds/1/stop"),
        client.post("/api/rounds/1/approve"),
        client.post("/api/rounds/1/reject", json={"reason": "No."}),
    ]
    assert {r.status_code for r in refused} == {403}
    assert (
        "Only a handler who can sign off payouts over the limit"
        in refused[0].json()["detail"]
    )
    assert started == []
    assert resumed == []
    assert get_round(client)["state"] == "open"
    assert get_round(client)["pending"] is not None
    assert client.get("/api/rounds").status_code == 200


def test_the_round_actions_need_a_sign_in(client: TestClient, agents, started):
    agents.round_resume(lambda *_: None)
    del client.headers["Authorization"]
    for path, body in (
        ("/api/rounds", None),
        ("/api/rounds/1/stop", None),
        ("/api/rounds/1/approve", None),
        ("/api/rounds/1/reject", {"reason": "No."}),
    ):
        assert client.post(path, json=body).status_code == 401
    assert started == []


def test_approval_and_rejection_pass_the_handler_to_the_service(
    client: TestClient, agents
):
    resumed: list[tuple[int, str, str, str | None]] = []
    agents.round_resume(lambda *args: resumed.append(args))
    new_round(client)
    client.post(f"{BASE}/1/proposal", json=change())
    assert client.post("/api/rounds/1/approve").status_code == 202
    assert (
        client.post("/api/rounds/1/reject", json={"reason": "Too long."}).status_code
        == 202
    )
    assert resumed == [
        (1, "approved", "HND-01", None),
        (1, "rejected", "HND-01", "Too long."),
    ]
    # The backend records nothing of its own: the proposal is still waiting.
    assert get_round(client)["pending"] is not None
    assert get_round(client)["decisions"] == []


def test_a_refusal_from_the_service_is_passed_on_and_nothing_is_recorded(
    client: TestClient, agents
):
    def busy(*_: Any) -> None:
        raise HTTPException(409, "A dispatch is running")

    agents.round_resume(busy)
    new_round(client)
    client.post(f"{BASE}/1/proposal", json=change())
    refused = client.post("/api/rounds/1/approve")
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "A dispatch is running",
    )
    assert get_round(client)["decisions"] == []
    assert len(versions(client)) == 1


def test_approving_a_round_that_is_not_open_is_refused_before_the_service(
    client: TestClient, agents
):
    called: list[int] = []
    agents.round_resume(lambda rid, *_: called.append(rid))
    new_round(client)
    client.post("/api/rounds/1/stop")
    assert client.post("/api/rounds/1/approve").status_code == 409
    assert called == []
    assert client.post("/api/rounds/9/approve").status_code == 404


def test_the_rounds_are_listed_newest_first(client: TestClient):
    assert client.get("/api/rounds/latest").json() is None
    new_round(client)
    client.post(f"{BASE}/1/end", json={"state": "finished", "why": None})
    new_round(client)
    assert [r["id"] for r in client.get("/api/rounds").json()] == [2, 1]
    assert client.get("/api/rounds/latest").json()["id"] == 2
    assert client.post(BASE, json={"handler_id": "HND-99"}).status_code == 404


def test_progress_is_recorded_as_it_happens(client: TestClient):
    new_round(client)
    for stage, done, of in (
        ("first_evaluations", 1, 3),
        ("improver_working", 0, 0),
        ("evaluating", 2, 3),
        ("verdict", 0, 0),
    ):
        got = client.post(
            f"{BASE}/1/stage", json={"stage": stage, "done": done, "of": of}
        ).json()
        assert (got["stage"], got["done"], got["of"]) == (stage, done, of)
