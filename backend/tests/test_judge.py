import os
import sqlite3
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openrouter.components import (
    DecisionsNoulAnswer,
    DecisionsResponse,
    DecisionsResponseUsage,
    DecisionsScoreAnswer,
)

from backend import judge
from backend.tests.test_evaluations import (
    SECTION,
    TINY_HELD_OUT,
    create,
    end,
    record,
    work,
)

CASE = judge.Case(
    "approve", "Covered.", "3. Trip Delay", "approve", "Fine.", "3. Trip Delay"
)


def response(
    score: float, probabilities: dict[str, float], grounds: float, section: float
) -> DecisionsResponse:
    return DecisionsResponse(
        answers={
            "grade": DecisionsScoreAnswer(
                type="score", score=score, probabilities=probabilities
            ),
            "same_grounds": DecisionsNoulAnswer(type="noul", noul=grounds),
            "same_section": DecisionsNoulAnswer(type="noul", noul=section),
        },
        model=judge.JEV,
        usage=DecisionsResponseUsage(input_tokens=10, output_tokens=2, cost=0.0007),
    )


class FakeClient:
    """Stands in for the OpenRouter client and keeps what it was asked."""

    asked: list[dict[str, Any]] = []
    reply: DecisionsResponse

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.alpha = self
        self.decisions = self

    def create(self, **kwargs: Any) -> DecisionsResponse:
        FakeClient.asked.append({**self.kwargs, **kwargs})
        return FakeClient.reply


@pytest.fixture
def fake_openrouter(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    FakeClient.asked = []
    monkeypatch.setattr(judge, "OpenRouter", FakeClient)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return FakeClient


def test_the_letter_is_the_most_likely_level_not_the_score_rounded(fake_openrouter):
    # The score, 1.4, would round to C, but B is the most likely level.
    fake_openrouter.reply = response(
        1.4, {"0": 0.1, "1": 0.2, "2": 0.6, "3": 0.1}, grounds=0.9, section=0.2
    )
    graded = judge.grade(CASE)
    assert graded == judge.Grade(
        grade=1.4, letter="B", same_grounds=True, same_section=False, cost=0.0007
    )


def test_the_call_is_made_once_with_the_address_a_timeout_and_no_retries(
    fake_openrouter, monkeypatch: pytest.MonkeyPatch
):
    fake_openrouter.reply = response(3, {"0": 0, "1": 0, "2": 0, "3": 1}, 1, 1)
    monkeypatch.setenv("OPENROUTER_SERVER_URL", "http://127.0.0.1:8005")
    judge.grade(CASE)
    asked = fake_openrouter.asked[0]
    assert asked["server_url"] == "http://127.0.0.1:8005"
    assert (asked["timeout_ms"], asked["retries"]) == (10_000, None)
    assert asked["model"] == "typesafe/jev-1.13"
    assert asked["state"]["handler"]["reference"] == "3. Trip Delay"
    assert asked["state"]["agent"] == {
        "decision": "approve",
        "reason": "Fine.",
        "section": "3. Trip Delay",
    }
    assert set(asked["questions"]) == {"grade", "same_grounds", "same_section"}
    monkeypatch.delenv("OPENROUTER_SERVER_URL")
    assert judge.server_url() == "https://openrouter.ai"


def test_an_answer_that_is_not_the_three_questions_is_an_error(fake_openrouter):
    fake_openrouter.reply = response(1, {}, 0.5, 0.5)
    with pytest.raises(ValueError, match="did not answer"):
        judge.grade(CASE)


def test_the_handlers_reasons_are_stored_with_the_answer_key(client: TestClient):
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        empty = conn.execute(
            "SELECT COUNT(*) FROM answer_key WHERE reason = '' OR reference = ''"
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) FROM answer_key").fetchone()
    assert empty == (0,)
    assert total == (50,)


class Judge:
    """A stub judge that records the cases it is asked about."""

    def __init__(self) -> None:
        self.cases: list[judge.Case] = []
        self.fail: set[str] = set()
        self.delay = 0.0
        self.lock = threading.Lock()

    def __call__(self, case: judge.Case) -> judge.Grade:
        with self.lock:
            self.cases.append(case)
        time.sleep(self.delay)
        if case.agent_reason in self.fail:
            raise TimeoutError("too slow")
        return judge.Grade(2.0, "B", True, False, 0.001)


@pytest.fixture
def stub_judge(monkeypatch: pytest.MonkeyPatch) -> Judge:
    stub = Judge()
    monkeypatch.setattr(judge, "grade", stub)
    return stub


def finished_evaluation(client: TestClient, undecided: int = 1) -> dict[str, Any]:
    run = create(client)
    run_id = run["run_id"]
    copies = [f"EV{run_id}-{source}" for source in TINY_HELD_OUT]
    for position, copy in enumerate(copies):
        answer = None if position < undecided else ("approve", 100.0)
        work(client, run_id, copy, answer)
        record(client, run_id, copy, 0.002, 1.0)
    end(client, run_id)
    return client.get(f"/api/evaluations/{run_id}").json()


def test_an_undecided_claim_gets_f_without_asking_the_judge(
    client: TestClient, stub_judge: Judge
):
    detail = finished_evaluation(client)
    assert len(stub_judge.cases) == 4
    by_claim = {c["source_id"]: c for c in detail["results"]}
    undecided = by_claim[TINY_HELD_OUT[0]]
    decided = [c for source, c in by_claim.items() if source != TINY_HELD_OUT[0]]
    assert (undecided["grade"], undecided["letter"]) == (0.0, "F")
    assert undecided["same_grounds"] is None
    assert [c["letter"] for c in decided] == ["B"] * 4
    assert decided[0]["same_grounds"] is True
    assert decided[0]["same_section"] is False
    assert detail["measures"]["grade"] == pytest.approx((0.0 + 4 * 2.0) / 5)


def test_the_judge_sees_the_handlers_reason_and_the_agents_explanation(
    client: TestClient, stub_judge: Judge
):
    finished_evaluation(client)
    for case in stub_judge.cases:
        assert case.handler_reason
        assert case.handler_reference
        assert case.handler_decision in ("approve", "decline", "refer")
        assert (case.agent_reason, case.agent_section) == ("Stub.", SECTION)
    assert len({c.handler_reason for c in stub_judge.cases}) == 4


def test_a_claim_the_judge_could_not_grade_is_left_out_of_the_average(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls = {"n": 0}
    lock = threading.Lock()

    def sometimes(case: judge.Case) -> judge.Grade:
        with lock:
            calls["n"] += 1
            nth = calls["n"]
        if nth == 1:
            raise TimeoutError("too slow")
        return judge.Grade(3.0, "A", True, True, 0.001)

    monkeypatch.setattr(judge, "grade", sometimes)
    detail = finished_evaluation(client, undecided=0)
    graded = [c for c in detail["results"] if c["grade"] is not None]
    assert len(graded) == 4
    assert [c for c in detail["results"] if c["grade"] is None][0]["letter"] is None
    assert detail["measures"]["grade"] == pytest.approx(3.0)
    assert detail["measures"]["judge_cost"] == pytest.approx(0.004)


def test_a_run_with_no_graded_claim_has_no_grade(client: TestClient, stub_judge: Judge):
    stub_judge.fail = {"Stub."}
    detail = finished_evaluation(client, undecided=0)
    assert detail["measures"]["grade"] is None
    assert detail["measures"]["judge_cost"] == 0


def test_the_judges_cost_is_kept_apart_from_the_claim_agents(
    client: TestClient, stub_judge: Judge
):
    detail = finished_evaluation(client)
    measures = detail["measures"]
    assert measures["judge_cost"] == pytest.approx(4 * 0.001)
    assert measures["cost_per_claim"] == pytest.approx(0.002)


def test_twenty_five_claims_are_graded_at_once(client: TestClient, stub_judge: Judge):
    run_id = create(client, "evaluation", False)["run_id"]
    for source in client.get(f"/api/evaluations/{run_id}").json()["results"]:
        work(client, run_id, source["claim_id"], ("approve", 50.0))
    stub_judge.delay = 1.0
    started = time.monotonic()
    end(client, run_id)
    elapsed = time.monotonic() - started
    assert len(stub_judge.cases) == 25
    assert elapsed < 6


def test_a_stopped_evaluation_is_not_graded(client: TestClient, stub_judge: Judge):
    run_id = create(client)["run_id"]
    work(client, run_id, f"EV{run_id}-{TINY_HELD_OUT[0]}", ("approve", 10.0))
    client.post("/api/runs/stop")
    detail = client.get(f"/api/evaluations/{run_id}").json()
    assert stub_judge.cases == []
    assert detail["measures"]["grade"] is None
    failed = create(client)["run_id"]
    end(client, failed, "failed")
    assert stub_judge.cases == []


def test_with_a_model_name_the_judge_is_not_called(
    client: TestClient, stub_judge: Judge, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    detail = finished_evaluation(client, undecided=0)
    assert stub_judge.cases == []
    assert all(c["grade"] is None for c in detail["results"])
    assert detail["measures"]["grade"] is None


def test_a_queue_claim_shows_nothing_from_the_judge(client: TestClient):
    claim = client.get("/api/claims/CLM-1001").json()
    assert "grade" not in claim
    assert "reason" not in claim
