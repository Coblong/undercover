import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import main
from agent.tests.stubs import StubModel, calls, decision, stub

pytestmark = pytest.mark.usefixtures("in_process_backend")


def rows(tmp_path: Path, table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]  # noqa: S608


def priced(message: AIMessage, tokens: tuple[int, int], cost: float) -> AIMessage:
    """A model reply with the tokens and the cost OpenRouter reports."""
    message.usage_metadata = {
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "total_tokens": sum(tokens),
    }
    message.response_metadata = {"token_usage": {"cost": cost}}
    return message


@pytest.fixture
def run_id(client: TestClient, tmp_path: Path) -> int:
    """A running dispatch, inserted directly, for a run id to record against."""
    with sqlite3.connect(tmp_path / "test.db") as conn:
        return conn.execute(
            "INSERT INTO runs (kind, concurrency, model, reasoning_effort,"
            " config_version, state, started_at) VALUES ('dispatch', 1, 'm', 'low',"
            " 1, 'running',"
            " '2026-11-03T10:00:00+00:00')"
        ).lastrowid  # pyright: ignore[reportReturnType]


def test_each_model_call_records_its_tokens_and_cost_with_the_run(
    client: TestClient, tmp_path: Path, run_id: int
):
    replies = [
        priced(calls(("get_claim", {})), (1000, 20), 0.0012),
        priced(calls(decision("approve", 420)), (1500, 60), 0.0021),
    ]
    main.run("CLM-1001", "t", stub(*replies), run_id=run_id)
    recorded = rows(tmp_path, "model_calls")
    assert [(r["claim_id"], r["run_id"]) for r in recorded] == [
        ("CLM-1001", run_id)
    ] * 2
    assert [(r["input_tokens"], r["output_tokens"]) for r in recorded] == [
        (1000, 20),
        (1500, 60),
    ]
    assert [r["cost"] for r in recorded] == [0.0012, 0.0021]


def test_a_reply_without_a_price_records_zero(client: TestClient, tmp_path: Path):
    main.run("CLM-1002", "t", stub(calls(decision("decline", 0))))
    (recorded,) = rows(tmp_path, "model_calls")
    assert (recorded["input_tokens"], recorded["cost"], recorded["run_id"]) == (
        0,
        0,
        None,
    )


class Failing(StubModel):
    """A stub model whose first calls raise."""

    failures: int = 1
    tried: int = 0

    def _generate(self, messages, *args, **kwargs):
        self.tried += 1
        if self.tried <= self.failures:
            raise httpx.ConnectError("the model is down")
        return super()._generate(messages, *args, **kwargs)


def test_a_call_that_raises_records_nothing_and_a_retry_records_one(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    reply = priced(calls(decision("decline", 0)), (900, 30), 0.001)
    model = Failing(messages=iter([reply]))
    main.run("CLM-1002", "t", model)
    assert len(rows(tmp_path, "model_calls")) == 1


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Make the agent's clock read the times a test gives, two for each run."""
    times: list[float] = []
    ticks = iter(times)
    monkeypatch.setattr(main, "clock", lambda: next(ticks))
    return times


def test_time_is_recorded_when_the_graph_raises(
    client: TestClient,
    tmp_path: Path,
    clock: list[float],
    monkeypatch: pytest.MonkeyPatch,
):
    clock.extend([10.0, 14.5])
    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    with pytest.raises(httpx.ConnectError):
        main.run("CLM-1002", "t", Failing(messages=iter([]), failures=10))
    (recorded,) = rows(tmp_path, "work_times")
    assert recorded["seconds"] == 4.5


def test_the_wait_for_a_sign_off_is_in_neither_stretch(
    client: TestClient, tmp_path: Path, clock: list[float], run_id: int
):
    clock.extend([0.0, 2.0, 100.0, 103.0])
    saver = InMemorySaver()
    main.run(
        "CLM-1003",
        "t",
        stub(calls(decision("approve", 1250))),
        checkpointer=saver,
        run_id=run_id,
    )
    client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    main.run("CLM-1003", "t", stub(), checkpointer=saver)
    recorded = rows(tmp_path, "work_times")
    assert [(r["seconds"], r["run_id"]) for r in recorded] == [
        (2.0, run_id),
        (3.0, None),
    ]


def test_a_run_that_does_not_exist_is_a_404(client: TestClient):
    body = {"run_id": 99, "input_tokens": 1, "output_tokens": 1, "cost": 0.1}
    assert client.post("/api/claims/CLM-1001/model-calls", json=body).status_code == 404
    time = {"run_id": 99, "seconds": 1.0}
    assert client.post("/api/claims/CLM-1001/work-times", json=time).status_code == 404
    missing = client.post(
        "/api/claims/CLM-9999/work-times", json={"run_id": None, "seconds": 1}
    )
    assert missing.status_code == 404


def test_negative_or_unreasonable_numbers_are_refused(client: TestClient):
    body = {"run_id": None, "input_tokens": 1, "output_tokens": 1, "cost": -0.1}
    assert client.post("/api/claims/CLM-1001/model-calls", json=body).status_code == 422
    body = {"run_id": None, "input_tokens": -1, "output_tokens": 1, "cost": 0.1}
    assert client.post("/api/claims/CLM-1001/model-calls", json=body).status_code == 422
    time = {"run_id": None, "seconds": -1}
    assert client.post("/api/claims/CLM-1001/work-times", json=time).status_code == 422


def reply_with(usage: dict[str, Any]) -> AIMessage:
    message = AIMessage(content="")
    message.response_metadata = {"token_usage": usage}
    return message


def test_a_reply_costs_what_openrouter_reports():
    assert main.reply_cost(reply_with({"cost": 0.0042})) == 0.0042
    assert main.reply_cost(reply_with({"cost": 0.0042, "is_byok": False})) == 0.0042
    assert main.reply_cost(AIMessage(content="")) == 0.0


def test_a_reply_on_our_own_key_adds_what_the_provider_charged():
    usage = {
        "cost": 0.0001,
        "is_byok": True,
        "cost_details": {"upstream_inference_cost": 0.0037},
    }
    assert main.reply_cost(reply_with(usage)) == pytest.approx(0.0038)
    free_fee = {
        "cost": 0,
        "is_byok": True,
        "cost_details": {"upstream_inference_cost": 3.7e-06},
    }
    assert main.reply_cost(reply_with(free_fee)) == pytest.approx(3.7e-06)
    assert main.reply_cost(reply_with({"cost": 0, "is_byok": True})) == 0
