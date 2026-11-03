import itertools
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import RetryPolicy

from agent import dispatcher, main
from agent.tests.stubs import StubModel, calls, decision
from backend.work import AgentStopped
from conftest import Agents

pytestmark = pytest.mark.usefixtures("in_process_backend")

FAST = RetryPolicy(
    max_attempts=2,
    initial_interval=0.01,
    jitter=False,
    retry_on=(ModelRateLimitError,),
)


def new_dispatch(client: TestClient, concurrency: int = 3) -> int:
    body = {
        "concurrency": concurrency,
        "model": "openai/gpt-6-luna",
        "reasoning_effort": "low",
    }
    return client.post("/api/runs/dispatch", json=body).json()["run_id"]


def rows(tmp_path: Path, sql: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(tmp_path / "test.db") as conn:
        return conn.execute(sql).fetchall()


class Meter:
    """A stand-in worker that sleeps, and notes how many work at once."""

    def __init__(self) -> None:
        self.live = 0
        self.peak = 0
        self.started: list[str] = []
        self.lock = threading.Lock()

    def __call__(self, claim_id: str, run_id: int) -> str:
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
            self.started.append(claim_id)
        time.sleep(0.05)
        with self.lock:
            self.live -= 1
        return "decided"


@pytest.mark.parametrize(("concurrency", "peak"), [(3, 3), (25, 25), (1, 1)])
def test_never_more_than_n_claims_are_worked_at_once(
    client: TestClient, concurrency: int, peak: int
):
    meter = Meter()
    dispatcher.run_dispatch(new_dispatch(client, concurrency), concurrency, meter)
    assert len(meter.started) == 25
    assert meter.peak == peak


def test_claims_start_in_order_oldest_submitted_first(
    client: TestClient, agents: Agents
):
    claims = client.get("/api/claims").json()
    oldest_first = [
        c["claim_id"]
        for c in sorted(claims, key=lambda c: (c["submitted_on"], c["claim_id"]))
    ]
    middle = oldest_first[12]

    def stopped(claim_id: str, thread_id: str) -> str:
        raise AgentStopped("killed")

    agents.claim(stopped)
    client.post(f"/api/claims/{middle}/run")
    assert client.get(f"/api/claims/{middle}").json()["state"] == "in_progress"
    meter = Meter()
    dispatcher.run_dispatch(new_dispatch(client, 1), 1, meter)
    assert meter.started == oldest_first


def only(claim_id: str, real: Callable[[str, int], str]) -> Callable[[str, int], str]:
    """Work one claim for real and the rest with a stand-in."""
    return lambda c, r: real(c, r) if c == claim_id else "decided"


class RateLimited(StubModel):
    """A stub model that is rate limited for its first calls."""

    failures: int = 0
    tried: int = 0

    def _generate(self, messages, *args, **kwargs):
        self.tried += 1
        if self.tried <= self.failures:
            raise ModelRateLimitError("429")
        return super()._generate(messages, *args, **kwargs)


class Broken(StubModel):
    """A stub model that fails with an ordinary error."""

    tried: int = 0

    def _generate(self, messages, *args, **kwargs):
        self.tried += 1
        raise ValueError("bad reply")


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "RETRY_DELAY", 0)


def real_work(saver: InMemorySaver, llm: StubModel):
    return lambda c, r: main.work_claim(c, r, lambda: saver, llm)


def test_a_rate_limited_claim_carries_on_as_the_same_attempt(
    client: TestClient, tmp_path: Path
):
    model = RateLimited(
        messages=iter([calls(decision("approve", 420))]), failures=main.MODEL_TRIES
    )
    work = only("CLM-1001", real_work(InMemorySaver(), model))
    dispatcher.run_dispatch(new_dispatch(client), 3, work, FAST)
    assert model.tried == main.MODEL_TRIES + 1
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["state"] == "decided"
    assert claim["payment"]["amount"] == 420.0
    assert rows(tmp_path, "SELECT failed FROM attempts") == [(0,)]
    assert rows(tmp_path, "SELECT COUNT(*) FROM decisions") == [(1,)]
    assert rows(tmp_path, "SELECT COUNT(*) FROM payments") == [(1,)]
    assert len(rows(tmp_path, "SELECT DISTINCT thread_id FROM attempts")) == 1


def test_a_claim_that_is_still_rate_limited_stays_in_progress_for_the_next_dispatch(
    client: TestClient, tmp_path: Path
):
    saver = InMemorySaver()
    model = RateLimited(
        messages=iter([calls(decision("approve", 420))]), failures=2 * main.MODEL_TRIES
    )
    first = new_dispatch(client)
    dispatcher.run_dispatch(first, 3, only("CLM-1001", real_work(saver, model)), FAST)
    assert client.get("/api/claims/CLM-1001").json()["state"] == "in_progress"
    attempts = rows(tmp_path, "SELECT thread_id, failed FROM attempts")
    assert len(attempts) == 1
    assert attempts[0][1] is None
    client.post(f"/api/runs/{first}/end", json={"state": "finished"})

    second = new_dispatch(client)
    dispatcher.run_dispatch(second, 3, only("CLM-1001", real_work(saver, model)), FAST)
    assert client.get("/api/claims/CLM-1001").json()["state"] == "decided"
    assert rows(tmp_path, "SELECT thread_id, failed FROM attempts") == [
        (attempts[0][0], 0)
    ]
    assert rows(tmp_path, "SELECT COUNT(*) FROM decisions") == [(1,)]


def test_a_claim_that_fails_goes_back_and_is_tried_once_more_in_the_same_dispatch(
    client: TestClient, tmp_path: Path
):
    model = Broken(messages=iter([]))
    run_id = new_dispatch(client)
    work = only("CLM-1001", real_work(InMemorySaver(), model))
    dispatcher.run_dispatch(run_id, 3, work, FAST)
    assert model.tried == 2 * main.MODEL_TRIES
    assert client.get("/api/claims/CLM-1001").json()["state"] == "left_for_handler"
    assert rows(tmp_path, "SELECT failed FROM attempts") == [(1,), (1,)]
    assert client.get("/api/claims/CLM-1002").json()["state"] == "waiting"


def test_one_bad_claim_never_stops_the_dispatch(client: TestClient):
    def work(claim_id: str, run_id: int) -> str:
        if claim_id == "CLM-1005":
            raise RuntimeError("the backend fell over")
        return "decided"

    run_id = new_dispatch(client)
    meter_calls: list[str] = []

    def recording(claim_id: str, run_id: int) -> str:
        meter_calls.append(claim_id)
        return work(claim_id, run_id)

    dispatcher.run_dispatch(run_id, 3, recording, FAST)
    assert len(meter_calls) == 25
    assert meter_calls.count("CLM-1005") == 1


def test_a_claim_with_an_earlier_failure_is_left_without_a_second_round(
    client: TestClient, tmp_path: Path
):
    earlier = new_dispatch(client)
    client.post(f"/api/runs/{earlier}/plan")
    client.post(f"/api/runs/{earlier}/claims/CLM-1001/start")
    client.post("/api/claims/CLM-1001/attempt/end", json={"error": "first"})
    client.post(f"/api/runs/{earlier}/end", json={"state": "finished"})

    model = Broken(messages=iter([]))
    worked: list[str] = []
    real = real_work(InMemorySaver(), model)

    def work(claim_id: str, run_id: int) -> str:
        worked.append(claim_id)
        return real(claim_id, run_id) if claim_id == "CLM-1001" else "decided"

    dispatcher.run_dispatch(new_dispatch(client), 3, work, FAST)
    assert worked.count("CLM-1001") == 1
    assert model.tried == main.MODEL_TRIES
    assert client.get("/api/claims/CLM-1001").json()["state"] == "left_for_handler"


def test_a_claim_the_service_is_already_working_is_skipped(
    client: TestClient, tmp_path: Path
):
    run_id = new_dispatch(client)
    client.post(f"/api/runs/{run_id}/plan")
    model = StubModel(messages=iter([]))
    with main.working("CLM-1001"):
        state = main.work_claim("CLM-1001", run_id, InMemorySaver, model)
    assert state == "in_progress"
    assert rows(tmp_path, "SELECT COUNT(*) FROM attempts") == [(0,)]


def test_a_claim_held_for_a_sign_off_does_not_hold_up_the_others(
    client: TestClient, tmp_path: Path
):
    saver = InMemorySaver()
    model = StubModel(messages=iter([calls(decision("approve", 1250))]))
    meter = Meter()
    real = real_work(saver, model)

    def work(claim_id: str, run_id: int) -> str:
        return (
            real(claim_id, run_id)
            if claim_id == "CLM-1003"
            else meter(claim_id, run_id)
        )

    dispatcher.run_dispatch(new_dispatch(client), 3, work, FAST)
    assert client.get("/api/claims/CLM-1003").json()["state"] == "waiting_for_sign_off"
    assert len(meter.started) == 24

    ((thread,),) = rows(tmp_path, "SELECT thread_id FROM attempts")
    client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    reply = main.run("CLM-1003", thread, StubModel(messages=iter([])), saver)
    assert reply == "approve 1250.00, paid"
    assert rows(tmp_path, "SELECT COUNT(*) FROM payments") == [(1,)]


def test_every_claim_gets_a_saver_of_its_own(client: TestClient):
    savers: list[InMemorySaver] = []

    def new_saver() -> InMemorySaver:
        savers.append(InMemorySaver())
        return savers[-1]

    replies: Any = (calls(decision("decline", 0)) for _ in itertools.count())
    model = StubModel(messages=replies)  # pyright: ignore[reportArgumentType]
    real = lambda c, r: main.work_claim(c, r, new_saver, model)  # noqa: E731
    chosen = {"CLM-1001", "CLM-1002", "CLM-1004"}
    work = lambda c, r: real(c, r) if c in chosen else "decided"  # noqa: E731
    dispatcher.run_dispatch(new_dispatch(client), 3, work, FAST)
    assert len(savers) == 3
    assert len({id(s) for s in savers}) == 3


def test_a_claim_that_pays_twice_is_impossible_when_a_retry_repeats_the_work(
    client: TestClient, tmp_path: Path
):
    message: AIMessage = calls(decision("approve", 420))
    model = RateLimited(messages=iter([message]), failures=main.MODEL_TRIES)
    work = only("CLM-1001", real_work(InMemorySaver(), model))
    dispatcher.run_dispatch(new_dispatch(client), 3, work, FAST)
    assert rows(tmp_path, "SELECT COUNT(*) FROM payments") == [(1,)]


def test_the_worker_retries_only_rate_limits_and_timeouts():
    assert dispatcher.RETRY.retry_on == (
        main.ModelRateLimitError,
        main.ModelTimeoutError,
    )
