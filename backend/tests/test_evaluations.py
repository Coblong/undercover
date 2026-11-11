import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend

SECTION = "3. Trip Delay"
TINY_HELD_OUT = ["CLM-1104", "CLM-1106", "CLM-1110", "CLM-1112", "CLM-1114"]
TINY_QUEUE = ["CLM-1007", "CLM-1015", "CLM-1017", "CLM-1020", "CLM-1021"]


def create(
    client: TestClient, eval_set: str = "held_out", tiny: bool = True, **changes: Any
) -> dict[str, Any]:
    body = {
        "concurrency": 5,
        "eval_set": eval_set,
        "tiny": tiny,
        **changes,
    }
    response = client.post("/api/runs/evaluation", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def db(tmp_path: Path, sql: str, *values: Any) -> list[tuple[Any, ...]]:
    with sqlite3.connect(tmp_path / "test.db") as conn:
        return conn.execute(sql, values).fetchall()


def end(client: TestClient, run_id: int, state: str = "finished") -> None:
    client.post(f"/api/runs/{run_id}/end", json={"state": state})


def work(client: TestClient, run_id: int, copy: str, answer: tuple[str, float] | None):
    """Play out the agent's work on a copy through the API."""
    assert client.post(f"/api/runs/{run_id}/claims/{copy}/start").status_code == 200
    if answer:
        body = {
            "decision": answer[0],
            "payout": answer[1],
            "explanation": "Stub.",
            "policy_section": SECTION,
        }
        assert client.post(f"/api/claims/{copy}/decision", json=body).status_code == 200
    client.post(f"/api/claims/{copy}/attempt/end", json={"error": None})


def record(client: TestClient, run_id: int, copy: str, cost: float, seconds: float):
    model = {"run_id": run_id, "input_tokens": 1, "output_tokens": 1, "cost": cost}
    assert client.post(f"/api/claims/{copy}/model-calls", json=model).status_code == 200
    time = {"run_id": run_id, "seconds": seconds}
    assert client.post(f"/api/claims/{copy}/work-times", json=time).status_code == 200


def test_seeding_keeps_the_held_out_claims_and_the_answer_key_apart(
    client: TestClient, tmp_path: Path
):
    assert db(tmp_path, "SELECT COUNT(*) FROM claims") == [(25,)]
    assert db(tmp_path, "SELECT COUNT(*) FROM held_out_claims") == [(25,)]
    assert db(
        tmp_path,
        "SELECT COUNT(*) FROM held_out_claims h JOIN claims c USING (claim_id)",
    ) == [(0,)]
    counts = db(
        tmp_path, "SELECT eval_set, COUNT(*), SUM(tiny) FROM answer_key GROUP BY 1"
    )
    assert counts == [("evaluation", 25, 5), ("held_out", 25, 5)]
    assert len(client.get("/api/claims").json()) == 25
    assert client.get("/api/claims/CLM-1101").status_code == 404


def test_only_the_evaluations_and_the_seeding_know_the_answer_key():
    allowed = {"evaluations.py", "db.py", "tables.py"}
    for path in Path(backend.__file__).parent.glob("*.py"):
        text = path.read_text()
        if re.search(r"answer_key|held_out_claims", text):
            assert path.name in allowed, path.name


def test_an_evaluation_makes_its_own_copies_with_new_ids(client: TestClient):
    queue_before = client.get("/api/claims").json()
    run = create(client)
    assert run["state"] == "running"
    assert (run["eval_set"], run["tiny"], run["concurrency"]) == ("held_out", True, 5)
    assert (run["model"], run["reasoning_effort"]) == ("openai/gpt-6-luna", "low")
    assert run["claim_count"] == 5
    detail = client.get(f"/api/evaluations/{run['run_id']}").json()
    copies = [c["claim_id"] for c in detail["results"]]
    assert sorted(copies) == sorted(f"EV1-{source}" for source in TINY_HELD_OUT)
    assert {c["source_id"] for c in detail["results"]} == set(TINY_HELD_OUT)
    assert client.get("/api/claims").json() == queue_before
    copy = client.get(f"/api/claims/{copies[0]}").json()
    assert copy["claim_id"] == copies[0]
    assert copy["state"] == "waiting"
    assert copy["evaluation_id"] == run["run_id"]
    assert client.get("/api/claims/CLM-1001").json()["evaluation_id"] is None


def test_the_whole_set_or_just_the_tiny_claims(client: TestClient):
    tiny = create(client, "evaluation", True)
    assert tiny["claim_count"] == 5
    end(client, tiny["run_id"])
    whole = create(client, "evaluation", False)
    assert whole["claim_count"] == 25
    end(client, whole["run_id"])
    held_out = create(client, "held_out", False)
    assert held_out["claim_count"] == 25
    detail = client.get(f"/api/evaluations/{tiny['run_id']}").json()
    assert {c["source_id"] for c in detail["results"]} == set(TINY_QUEUE)


def test_a_copy_holds_nothing_from_the_handlers_decisions(client: TestClient):
    run = create(client)
    copy = client.get("/api/claims/EV1-CLM-1110").json()
    queue = client.get("/api/claims/CLM-1001").json()
    assert set(copy) == set(queue)
    assert copy["decision"] is None
    assert copy["payment"] is None
    assert "CLM-1110" not in str(copy["items"]) + str(copy["documents"])
    assert run["run_id"] == copy["evaluation_id"]


def test_one_run_at_a_time_includes_evaluations(client: TestClient):
    run = create(client)
    again = client.post(
        "/api/runs/evaluation",
        json={
            "concurrency": 1,
            "eval_set": "held_out",
            "tiny": True,
            "model": "m",
            "reasoning_effort": "low",
        },
    )
    assert again.status_code == 409
    dispatch = {"concurrency": 1, "model": "m", "reasoning_effort": "low"}
    assert client.post("/api/runs/dispatch", json=dispatch).status_code == 409
    end(client, run["run_id"])
    assert client.post("/api/runs/dispatch", json=dispatch).status_code == 201


def test_a_dispatch_never_works_a_copy(client: TestClient):
    run = create(client, "evaluation", False)
    end(client, run["run_id"])
    dispatch = client.post(
        "/api/runs/dispatch",
        json={"concurrency": 3, "model": "m", "reasoning_effort": "low"},
    ).json()
    planned = client.post(f"/api/runs/{dispatch['run_id']}/plan").json()["claim_ids"]
    assert len(planned) == 25
    assert not any(claim.startswith("EV") for claim in planned)


def history(client: TestClient, claim_id: str) -> list[str]:
    found = client.get(f"/api/claims/{claim_id}/claim-history").json()
    return [p["claim_id"] for p in found["previous_claims"]]


def test_a_copy_sees_only_its_own_evaluation_and_a_queue_claim_sees_no_copy(
    client: TestClient,
):
    assert history(client, "CLM-1006").count("CLM-1005") == 1
    first = create(client, "evaluation", False)
    end(client, first["run_id"])
    second = create(client, "evaluation", False)
    one = history(client, "EV1-CLM-1006")
    two = history(client, "EV2-CLM-1006")
    assert "EV1-CLM-1005" in one
    assert not any(c.startswith("EV2") or c == "CLM-1005" for c in one)
    assert "EV2-CLM-1005" in two
    assert not any(c.startswith("EV1") or c == "CLM-1005" for c in two)
    queue = history(client, "CLM-1006")
    assert "CLM-1005" in queue
    assert not any(c.startswith("EV") for c in queue)
    assert second["run_id"] == 2


def test_a_copy_held_for_a_sign_off_counts_as_finished_and_cannot_be_signed_off(
    client: TestClient,
):
    run = create(client, "evaluation", True)
    copy = "EV1-CLM-1015"
    client.post(f"/api/runs/{run['run_id']}/claims/{copy}/start")
    body = {
        "decision": "approve",
        "payout": 1250,
        "explanation": "Covered.",
        "policy_section": SECTION,
    }
    assert client.post(f"/api/claims/{copy}/decision", json=body).status_code == 200
    ended = client.post(f"/api/claims/{copy}/attempt/end", json={"error": None})
    assert ended.json()["state"] == "decided"
    held = client.get(f"/api/claims/{copy}").json()
    assert held["state"] == "decided"
    assert held["payment"] is None
    assert held["needs_sign_off"] is True
    refused = client.post(f"/api/claims/{copy}/sign-off")
    assert refused.status_code == 409
    assert "copy made for an evaluation" in refused.json()["detail"]
    assert client.post(f"/api/claims/{copy}/run").status_code == 409
    start = client.post(f"/api/runs/{run['run_id']}/claims/EV1-CLM-1007/start").json()
    assert start["evaluation"] is True


def test_a_decision_on_a_waiting_copy_settles_as_decided(client: TestClient):
    create(client, "evaluation", True)
    body = {
        "decision": "approve",
        "payout": 1250,
        "explanation": "Covered.",
        "policy_section": SECTION,
    }
    outcome = client.post("/api/claims/EV1-CLM-1015/decision", json=body)
    assert outcome.json()["state"] == "decided"


def test_the_queue_agents_claims_start_as_not_a_copy(client: TestClient):
    dispatch = {"concurrency": 1, "model": "m", "reasoning_effort": "low"}
    run_id = client.post("/api/runs/dispatch", json=dispatch).json()["run_id"]
    client.post(f"/api/runs/{run_id}/plan")
    start = client.post(f"/api/runs/{run_id}/claims/CLM-1001/start").json()
    assert start["evaluation"] is False


def test_a_stopped_evaluation_shows_stopped_and_counts_undecided_as_referrals(
    client: TestClient, tmp_path: Path
):
    run = create(client)
    client.post("/api/runs/stop")
    shown = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert shown["state"] == "stopped"
    assert shown["measures"]["run_seconds"] is None
    refers = db(
        tmp_path,
        "SELECT COUNT(*) FROM answer_key WHERE eval_set='held_out' AND tiny=1"
        " AND decision='refer'",
    )[0][0]
    assert refers == 1
    assert shown["measures"]["decision_accuracy"] == pytest.approx(1 / 5)
    assert shown["measures"]["straight_through"] == 0
    assert shown["measures"]["cost_of_mistakes"] == 4 * 50
    assert {c["agent_decision"] for c in shown["results"]} == {None}


def set_times(tmp_path: Path, run_id: int) -> None:
    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.execute(
            "UPDATE runs SET started_at='2026-11-03T10:00:00+00:00',"
            " ended_at='2026-11-03T10:01:30+00:00' WHERE id=?",
            (run_id,),
        )


def test_the_measures_match_ones_worked_out_by_hand(client: TestClient, tmp_path: Path):
    # Handlers: 1104 approve 250, 1106 approve 250, 1110 refer, 1112 approve 680,
    # 1114 decline.
    run = create(client)
    run_id = run["run_id"]
    answers: dict[str, tuple[str, float] | None] = {
        "CLM-1104": ("approve", 250.00),  # right
        "CLM-1106": ("approve", 250.99),  # right, within a dollar: costs 0.99
        "CLM-1110": None,  # not decided counts as a referral: right
        "CLM-1112": ("approve", 600.00),  # decision right, payout 80 off
        "CLM-1114": ("approve", 1250.00),  # wrongful approval, held, never paid
    }
    for number, (source, answer) in enumerate(answers.items(), start=1):
        copy = f"EV{run_id}-{source}"
        work(client, run_id, copy, answer)
        record(client, run_id, copy, cost=0.01 * number, seconds=10.0 * number)
    end(client, run_id)
    set_times(tmp_path, run_id)
    shown = client.get(f"/api/evaluations/{run_id}").json()
    measures = shown["measures"]
    assert measures["decision_accuracy"] == pytest.approx(4 / 5)
    assert measures["accuracy"] == pytest.approx(3 / 5)
    assert measures["cost_of_mistakes"] == pytest.approx(0 + 0.99 + 0 + 80 + 1250)
    assert measures["straight_through"] == pytest.approx(4 / 5)
    assert measures["cost_per_claim"] == pytest.approx(0.15 / 5)
    assert measures["seconds_per_claim"] == pytest.approx(150 / 5)
    assert measures["run_seconds"] == 90
    by_source = {c["source_id"]: c for c in shown["results"]}
    assert by_source["CLM-1112"]["right"] is False
    assert by_source["CLM-1112"]["decision_matches"] is True
    assert by_source["CLM-1106"]["right"] is True
    assert by_source["CLM-1106"]["mistake_cost"] == pytest.approx(0.99)
    assert by_source["CLM-1110"]["agent_decision"] is None
    assert by_source["CLM-1110"]["right"] is True
    assert by_source["CLM-1114"]["mistake_cost"] == 1250
    assert by_source["CLM-1114"]["state"] == "decided"
    assert by_source["CLM-1114"]["handler_decision"] == "decline"
    assert by_source["CLM-1104"]["reason"] == "Stub."
    assert by_source["CLM-1104"]["cost"] == pytest.approx(0.01)
    assert by_source["CLM-1104"]["seconds"] == 10


def test_a_wrongful_decline_a_needless_referral_and_a_payout_exactly_a_dollar_off(
    client: TestClient, tmp_path: Path
):
    run_id = create(client)["run_id"]
    answers: dict[str, tuple[str, float] | None] = {
        "CLM-1104": ("decline", 0),  # handler approved 250: wrongful decline
        "CLM-1106": ("refer", 0),  # handler approved 250: needless referral
        "CLM-1110": ("decline", 0),  # handler referred: wrongful decline
        "CLM-1112": ("approve", 681.00),  # handler approved 680: right, costs 1
        "CLM-1114": ("refer", 0),  # handler declined: needless referral
    }
    for source, answer in answers.items():
        work(client, run_id, f"EV{run_id}-{source}", answer)
    end(client, run_id)
    measures = client.get(f"/api/evaluations/{run_id}").json()["measures"]
    assert measures["decision_accuracy"] == pytest.approx(1 / 5)
    assert measures["accuracy"] == pytest.approx(1 / 5)
    assert measures["cost_of_mistakes"] == pytest.approx(1500 + 50 + 1500 + 1 + 50)
    assert measures["straight_through"] == pytest.approx(3 / 5)


def test_evaluations_are_listed_newest_first_with_their_settings(client: TestClient):
    assert client.get("/api/evaluations").json() == []
    first = create(client, "evaluation", True, concurrency=2)
    end(client, first["run_id"])
    second = create(client, "held_out", False, concurrency=9)
    listed = client.get("/api/evaluations").json()
    assert [e["run_id"] for e in listed] == [second["run_id"], first["run_id"]]
    assert (listed[0]["eval_set"], listed[0]["tiny"], listed[0]["concurrency"]) == (
        "held_out",
        False,
        9,
    )
    assert listed[0]["reasoning_effort"] == "low"
    assert listed[0]["config_version"] == 1
    assert listed[1]["claim_count"] == 5
    assert "results" not in listed[0]


def test_an_unknown_run_or_a_dispatch_is_not_an_evaluation(client: TestClient):
    assert client.get("/api/evaluations/9").status_code == 404
    dispatch = {"concurrency": 1, "model": "m", "reasoning_effort": "low"}
    run_id = client.post("/api/runs/dispatch", json=dispatch).json()["run_id"]
    assert client.get(f"/api/evaluations/{run_id}").status_code == 404


def test_a_dispatchs_totals_and_an_evaluations_stay_separate(client: TestClient):
    evaluation = create(client, "evaluation", False)
    run_id = evaluation["run_id"]
    record(client, run_id, "EV1-CLM-1005", 0.5, 20)
    record(client, run_id, "EV1-CLM-1006", 0.25, 10)
    end(client, run_id)
    dispatch = {"concurrency": 3, "model": "m", "reasoning_effort": "low"}
    dispatch_id = client.post("/api/runs/dispatch", json=dispatch).json()["run_id"]
    client.post(f"/api/runs/{dispatch_id}/plan")
    record(client, dispatch_id, "CLM-1005", 0.1, 4)
    latest = client.get("/api/dispatches/latest").json()
    assert latest["cost"] == pytest.approx(0.1)
    assert latest["claim_seconds"] == 4
    shown = client.get(f"/api/evaluations/{run_id}").json()
    assert shown["measures"]["cost_per_claim"] == pytest.approx(0.75 / 25)
    assert shown["measures"]["seconds_per_claim"] == pytest.approx(30 / 25)


def test_starting_an_evaluation_asks_the_service(client: TestClient, agents):
    asked: list[tuple[int, str, bool]] = []

    def starter(concurrency: int, eval_set: str, tiny: bool) -> int:
        asked.append((concurrency, eval_set, tiny))
        return create(client, eval_set, tiny, concurrency=concurrency)["run_id"]

    agents.evaluation(starter)
    response = client.post(
        "/api/evaluations",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": True},
    )
    assert response.status_code == 202
    assert asked == [(5, "held_out", True)]
    assert response.json()["state"] == "running"
    assert response.json()["claim_count"] == 5
    bad = {"concurrency": 5, "eval_set": "queue", "tiny": True}
    assert client.post("/api/evaluations", json=bad).status_code == 422
    assert (
        client.post(
            "/api/evaluations",
            json={"concurrency": 26, "eval_set": "held_out", "tiny": True},
        ).status_code
        == 422
    )


def test_a_service_that_is_not_running_cannot_start_an_evaluation(
    client: TestClient,
):
    response = client.post(
        "/api/evaluations",
        json={"concurrency": 5, "eval_set": "held_out", "tiny": True},
    )
    assert response.status_code == 503
