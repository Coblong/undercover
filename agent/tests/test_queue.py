import os
import sqlite3
from contextlib import closing
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import Field

from agent import main, queue
from agent.tests.stubs import StubModel, calls, decision, stub
from backend import runs
from backend.db import connect
from conftest import Agents

pytestmark = pytest.mark.usefixtures("in_process_backend")


class RecordingModel(StubModel):
    """A stub model that keeps the text of every message it is shown."""

    seen: list[str] = Field(default_factory=list)

    def _generate(self, messages, *args, **kwargs):
        self.seen.extend(str(m.content) for m in messages)
        return super()._generate(messages, *args, **kwargs)


def recording(*replies: AIMessage) -> RecordingModel:
    return RecordingModel(messages=iter(replies))


def new_run(limit: int) -> int:
    with closing(connect()) as conn:
        return runs.create(conn, limit).run_id


def todo(*items: tuple[str, str]) -> tuple[str, dict[str, Any]]:
    return (
        "write_todos",
        {"todos": [{"text": text, "status": status} for text, status in items]},
    )


def work(claim_id: str) -> AIMessage:
    return calls(("work_claim", {"claim_id": claim_id}))


def finish(summary: str = "Done.") -> AIMessage:
    return calls(("finish_run", {"summary": summary}))


def declines(claim_id: str) -> list[AIMessage]:
    return [
        calls(("get_claim", {})),
        calls(decision("decline", 0)),
        AIMessage(content="Declined."),
    ]


def claim_agents(agents: Agents, crashing: dict[str, int] | None = None) -> None:
    """Give every claim a stub agent that declines, or crashes some times first."""
    crashes = dict(crashing or {})

    def agent(claim_id: str, thread_id: str) -> str:
        if crashes.get(claim_id, 0) > 0:
            crashes[claim_id] -= 1
            raise RuntimeError("model unavailable")
        return main.run(claim_id, thread_id, stub(*declines(claim_id)))

    agents.claim(agent)


def get(client: TestClient, path: str) -> Any:
    return client.get(path).json()


def states(client: TestClient) -> dict[str, str]:
    return {c["claim_id"]: c["state"] for c in get(client, "/api/claims")}


def started_in_order() -> list[str]:
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        rows = conn.execute("SELECT claim_id FROM attempts ORDER BY id").fetchall()
    return [row[0] for row in rows]


@pytest.fixture
def todo_writes(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every to-do list the agent saves, as the backend held it right after."""
    saved: list[Any] = []
    real = main.call

    def spy(method: str, path: str, body: Any = None, timeout: float = 30) -> str:
        answer = real(method, path, body, timeout)
        if method == "PUT":
            saved.append(body["todos"])
        return answer

    monkeypatch.setattr(main, "call", spy)
    return saved


def test_a_run_plans_works_in_its_own_order_and_finishes(
    client: TestClient, todo_writes: list[Any], agents: Agents
):
    claim_agents(agents)
    run_id = new_run(3)
    model = recording(
        calls(("list_waiting_claims", {})),
        calls(
            todo(
                ("CLM-1003 passport", "in_progress"),
                ("CLM-1001 delay", "to_do"),
                ("CLM-1002 baggage", "to_do"),
            )
        ),
        work("CLM-1003"),
        calls(
            todo(
                ("CLM-1003 passport", "done"),
                ("CLM-1001 delay", "in_progress"),
                ("CLM-1002 baggage", "to_do"),
            )
        ),
        work("CLM-1001"),
        calls(
            todo(
                ("CLM-1003 passport", "done"),
                ("CLM-1001 delay", "done"),
                ("CLM-1002 baggage", "in_progress"),
            )
        ),
        work("CLM-1002"),
        calls(
            todo(
                ("CLM-1003 passport", "done"),
                ("CLM-1001 delay", "done"),
                ("CLM-1002 baggage", "done"),
            )
        ),
        finish("Three claims declined."),
        AIMessage(content="Finished."),
    )
    queue.run_queue(run_id, model)

    run = get(client, "/api/runs/latest")
    assert run["state"] == "finished"
    assert run["summary"] == "Three claims declined."
    assert [t["status"] for t in run["todos"]] == ["done", "done", "done"]
    assert started_in_order() == ["CLM-1003", "CLM-1001", "CLM-1002"]
    assert [[t["status"] for t in saved] for saved in todo_writes][:2] == [
        ["in_progress", "to_do", "to_do"],
        ["done", "in_progress", "to_do"],
    ]
    assert list(states(client).values()).count("waiting") == 22

    steps = get(client, "/api/claims/CLM-1001/steps")
    assert steps[0]["text"] == "get_claim()"
    story = get(client, "/api/claims/CLM-1001")["story"]
    assert story[:40] not in " ".join(model.seen)


def test_the_limit_leaves_the_rest_waiting(client: TestClient, agents: Agents):
    claim_agents(agents)
    model = recording(
        calls(todo(("CLM-1001", "in_progress"), ("CLM-1002", "to_do"))),
        work("CLM-1001"),
        work("CLM-1002"),
        work("CLM-1003"),
        finish(),
        AIMessage(content="Finished."),
    )
    queue.run_queue(new_run(2), model)
    assert list(states(client).values()).count("waiting") == 23
    assert "limit of 2 claims" in " ".join(model.seen)
    assert get(client, "/api/runs/latest")["state"] == "finished"


def test_a_failed_claim_does_not_stop_the_run_and_is_left_after_two_failures(
    client: TestClient, todo_writes: list[Any], agents: Agents
):
    claim_agents(agents, {"CLM-1002": 2})
    model = recording(
        work("CLM-1001"),
        work("CLM-1002"),
        work("CLM-1003"),
        work("CLM-1002"),
        calls(
            todo(
                ("CLM-1001", "done"),
                ("CLM-1002", "left_for_handler"),
                ("CLM-1003", "done"),
            )
        ),
        finish("Two decided, one left for a handler."),
        AIMessage(content="Finished."),
    )
    queue.run_queue(new_run(3), model)
    seen = " ".join(model.seen)
    assert "CLM-1002 failed (The agent stopped with an error (RuntimeError))" in seen
    assert "waiting to be tried again" in seen
    assert states(client)["CLM-1001"] == "decided"
    assert states(client)["CLM-1002"] == "left_for_handler"
    assert states(client)["CLM-1003"] == "decided"
    assert get(client, "/api/runs/latest")["todos"][1]["status"] == "left_for_handler"
    assert started_in_order() == ["CLM-1001", "CLM-1002", "CLM-1003", "CLM-1002"]


def test_finishing_early_is_refused_until_the_missed_claim_is_worked(
    client: TestClient, agents: Agents
):
    claim_agents(agents)
    model = recording(
        work("CLM-1001"),
        finish("All done."),
        work("CLM-1002"),
        finish("Two done."),
        AIMessage(content="Finished."),
    )
    queue.run_queue(new_run(2), model)
    assert "You have missed CLM-1002" in " ".join(model.seen)
    run = get(client, "/api/runs/latest")
    assert (run["state"], run["summary"]) == ("finished", "Two done.")
    assert started_in_order() == ["CLM-1001", "CLM-1002"]


def test_an_agent_that_stops_is_reminded_to_finish(client: TestClient, agents: Agents):
    claim_agents(agents)
    model = recording(
        work("CLM-1001"),
        AIMessage(content="I am done."),
        finish("One declined."),
        AIMessage(content="Finished."),
    )
    queue.run_queue(new_run(1), model)
    assert "The run is not finished" in " ".join(model.seen)
    assert get(client, "/api/runs/latest")["state"] == "finished"


def test_an_agent_that_never_finishes_fails_the_run(client: TestClient, agents: Agents):
    model = stub(*[AIMessage(content="I am done.")] * 3)
    agents.queue(lambda run_id: queue.run_queue(run_id, model))
    assert client.post("/api/runs", json={"limit": 1}).status_code == 202
    run = get(client, "/api/runs/latest")
    assert run["state"] == "failed"
    assert run["summary"] == (
        "The queue agent stopped with an error:"
        " the agent did not finish the run after 2 reminders"
    )


def test_the_tools_report_what_the_backend_refuses(client: TestClient):
    tools = {t.name: t for t in queue.queue_tools(new_run(1))}
    assert tools["finish_run"].invoke({"summary": "Done."}).startswith("Error 409")
    assert tools["write_todos"].invoke({"todos": []}) == "Saved 0 items."
    assert tools["work_claim"].invoke({"claim_id": "CLM-9999"}).startswith("Error 404")
    waiting = tools["list_waiting_claims"].invoke({})
    assert waiting.count("CLM-") == 25
    assert "story" not in waiting


def test_a_slow_claim_is_reported_not_raised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(
        main,
        "backend",
        lambda timeout=30: httpx.Client(
            transport=httpx.MockTransport(slow), base_url="http://backend"
        ),
    )
    tools = {t.name: t for t in queue.queue_tools(1)}
    answer = tools["work_claim"].invoke({"claim_id": "CLM-1001"})
    assert answer == "Error: the backend did not answer (ReadTimeout)"
