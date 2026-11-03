import ast
import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from agent import main
from agent.settings import AgentError
from agent.tests.stubs import StubModel, calls, decision, stub
from conftest import Agents

pytestmark = pytest.mark.usefixtures("in_process_backend")


def claim(client: TestClient, claim_id: str = "CLM-1001") -> dict[str, Any]:
    return client.get(f"/api/claims/{claim_id}").json()


def steps(client: TestClient, claim_id: str = "CLM-1001") -> list[dict[str, str]]:
    return client.get(f"/api/claims/{claim_id}/steps").json()


def tools_for(claim_id: str = "CLM-1001") -> dict[str, Any]:
    return {t.name: t for t in main.claim_tools(claim_id)}


def test_read_tools():
    tools = tools_for()
    assert "Rome" in tools["get_claim"].invoke({})
    assert '"plan":"premium"' in tools["get_policy"].invoke({})
    history = tools["get_claim_history"].invoke({})
    assert "CLM-0412" in history
    assert "email" not in history
    sections = tools["list_document_sections"].invoke({"document": "manual"})
    assert "Repeat claims" in sections
    section = tools["get_document_section"].invoke({"document": "wording", "number": 3})
    assert "Trip Delay" in section


def test_the_payout_tool_uses_the_rules_for_its_claim():
    lines = [
        {
            "description": "Hotel",
            "amount": 420,
            "quantity": 1,
            "kind": "expense",
            "nights": 0,
            "hours": 0,
        }
    ]
    result = tools_for()["calculate_payout"].invoke(
        {"benefit": "trip_delay", "lines": lines, "paid_by_others": 0}
    )
    assert json.loads(result)["payout"] == 420
    refused = tools_for("CLM-9999")["calculate_payout"].invoke(
        {"benefit": "trip_delay", "lines": lines, "paid_by_others": 0}
    )
    assert refused.startswith("Error 404")


def test_tools_return_the_reason_when_the_backend_refuses():
    assert tools_for("CLM-9999")["get_claim"].invoke({}).startswith("Error 404")
    assert tools_for("CLM-9999")["get_policy"].invoke({}).startswith("Error 404")
    refused = tools_for()["decide_claim"].invoke(
        {
            "decision": "approve",
            "payout": 5000,
            "explanation": "Covered.",
            "policy_section": "3. Trip Delay",
        }
    )
    assert refused.startswith("Error 422: The payout is more than the $420.00 claimed")


def test_the_tools_cannot_name_another_claim_or_customer():
    names = {"claim_id", "customer_id", "policy_id"}
    for tool in main.claim_tools("CLM-1001"):
        assert not names & set(tool.args), tool.name


def test_every_tool_reaches_only_its_own_claim(monkeypatch: pytest.MonkeyPatch):
    paths: list[str] = []
    real = main.call

    def spy(method: str, path: str, *args: Any, **kwargs: Any) -> str:
        paths.append(path)
        return real(method, path, *args, **kwargs)

    monkeypatch.setattr(main, "call", spy)
    tools = tools_for("CLM-1002")
    tools["get_claim"].invoke({})
    tools["get_policy"].invoke({})
    tools["get_claim_history"].invoke({})
    tools["decide_claim"].invoke(
        {
            "decision": "decline",
            "payout": 0,
            "explanation": "Not covered.",
            "policy_section": "9. General exclusions",
        }
    )
    own = [p for p in paths if "CLM-1002" in p]
    other = [p for p in paths if p not in own]
    assert len(own) == 4
    assert other == ["/api/policies/POL-2002"] or all("POL-" in p for p in other)


class RecordingModel(StubModel):
    """A stub model that counts the times it is called and keeps what it saw."""

    seen: list[list[str]] = Field(default_factory=list)

    def _generate(self, messages, *args, **kwargs):
        self.seen.append([str(m.content) for m in messages])
        return super()._generate(messages, *args, **kwargs)


def looping_calls() -> Iterator[AIMessage]:
    """Ask for the claim again and again, with a new message each time."""
    return (calls(("get_claim", {})) for _ in itertools.count())


class FlakyModel(StubModel):
    """A stub model whose first calls fail."""

    failures: int = 2
    tried: int = 0

    def _generate(self, messages, *args, **kwargs):
        self.tried += 1
        if self.tried <= self.failures:
            raise httpx.ConnectError("the model is down")
        return super()._generate(messages, *args, **kwargs)


def payments(client: TestClient, claim_id: str = "CLM-1001") -> Any:
    return claim(client, claim_id)["payment"]


def test_the_agent_has_no_tool_to_pay():
    assert "pay_claim" not in tools_for()
    assert "calculate_payout" in tools_for()


def test_an_approval_is_paid_straight_after_the_decision(client: TestClient):
    reply = main.run(
        "CLM-1001",
        "thread-1",
        stub(calls(("get_claim", {})), calls(decision("approve", 420))),
    )
    assert reply == "approve 420.00, paid"
    assert payments(client)["amount"] == 420.0
    assert [s["kind"] for s in steps(client)] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "message",
    ]
    assert steps(client)[0]["text"] == "get_claim()"
    assert steps(client)[-1]["text"] == "Paid $420.00."


def test_a_decline_ends_without_a_payment(client: TestClient):
    reply = main.run("CLM-1002", "thread-2", stub(calls(decision("decline", 0))))
    assert reply == "decline 0.00"
    done = claim(client, "CLM-1002")
    assert done["decision"]["decision"] == "decline"
    assert done["payment"] is None


def test_a_large_approval_pauses_for_a_sign_off(client: TestClient):
    saver = InMemorySaver()
    reply = main.run(
        "CLM-1003",
        "thread-3",
        stub(calls(decision("approve", 1250))),
        checkpointer=saver,
    )
    assert reply == "waiting for sign-off"
    held = claim(client, "CLM-1003")
    assert held["state"] == "waiting_for_sign_off"
    assert held["payment"] is None
    graph = main.ClaimNodes("CLM-1003", "x", stub()).compile(saver)
    snapshot = graph.get_state({"configurable": {"thread_id": "thread-3"}})
    assert snapshot.next == ("approval",)
    assert snapshot.interrupts[0].value == {"claim_id": "CLM-1003", "payout": 1250.0}
    assert steps(client, "CLM-1003")[-1]["text"] == (
        "Waiting for a handler to sign off $1,250.00."
    )


def test_a_thread_waiting_for_a_sign_off_stays_put_until_there_is_one(
    client: TestClient,
):
    saver = InMemorySaver()
    main.run(
        "CLM-1003", "t", stub(calls(decision("approve", 1250))), checkpointer=saver
    )
    assert (
        main.run("CLM-1003", "t", stub(), checkpointer=saver) == "waiting for sign-off"
    )
    assert payments(client, "CLM-1003") is None


def test_a_sign_off_resumes_the_thread_and_pays_once(client: TestClient):
    saver = InMemorySaver()
    main.run(
        "CLM-1003", "t", stub(calls(decision("approve", 1250))), checkpointer=saver
    )
    signed = client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    assert signed.status_code == 200
    reply = main.run("CLM-1003", "t", stub(), checkpointer=saver)
    assert reply == "approve 1250.00, paid"
    paid = payments(client, "CLM-1003")
    assert paid["amount"] == 1250.0
    assert paid["signed_off_by"] == "HND-01"
    assert main.run("CLM-1003", "t", stub(), checkpointer=saver) == reply
    assert claim(client, "CLM-1003")["payment"] == paid
    assert [s["text"] for s in steps(client, "CLM-1003")][-2:] == [
        "Signed off by Dana Whitfield.",
        "Paid $1,250.00.",
    ]


def test_a_stopped_claim_carries_on_from_its_last_step(client: TestClient):
    saver = InMemorySaver()
    nodes = main.ClaimNodes("CLM-1001", "Decide.", stub(calls(("get_claim", {}))))
    graph = nodes.compile(saver)
    config = {"configurable": {"thread_id": "t"}}
    first = {
        "messages": [("user", "Decide claim CLM-1001.")],
        "claim_id": "CLM-1001",
        "model_calls": 0,
        "stage": "undecided",
        "payout": 0.0,
        "approved_by": "",
        "reply": "",
    }
    for update in graph.stream(first, config, durability="sync"):  # pyright: ignore[reportArgumentType]
        if "tools" in update:
            break
    recorded = len(steps(client))
    assert recorded == 2
    assert claim(client)["decision"] is None

    model = RecordingModel(messages=iter([calls(decision("approve", 420))]))
    assert main.run("CLM-1001", "t", model, saver) == "approve 420.00, paid"
    assert len(model.seen) == 1
    assert any("Rome" in text for text in model.seen[0])
    assert [s["text"] for s in steps(client)].count("get_claim()") == 1
    assert claim(client)["decision"]["payout"] == 420.0
    assert payments(client)["amount"] == 420.0


def test_a_thread_on_a_claim_that_is_already_approved_pays_without_the_model(
    client: TestClient,
):
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": 420,
            "explanation": "Covered.",
            "policy_section": "3. Trip Delay",
        },
    )
    model = RecordingModel(messages=iter([]))
    assert main.run("CLM-1001", "fresh", model) == "approve 420.00, paid"
    assert model.seen == []
    assert payments(client)["amount"] == 420.0


def test_a_refused_decision_comes_back_to_the_model_as_text(client: TestClient):
    model = RecordingModel(
        messages=iter(
            [
                calls(decision("approve", 5000)),
                calls(decision("approve", 420)),
            ]
        )
    )
    assert main.run("CLM-1001", "t", model) == "approve 420.00, paid"
    assert any("Error 422: The payout is more than" in m for m in model.seen[1])
    assert len([s for s in steps(client) if s["kind"] == "tool_result"]) == 2


def test_a_failing_model_is_retried(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    model = FlakyModel(messages=iter([calls(decision("decline", 0))]))
    assert main.run("CLM-1002", "t", model) == "decline 0.00"
    assert model.tried == 3


def test_a_model_that_keeps_failing_stops_the_claim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    model = FlakyModel(messages=iter([]), failures=10)
    with pytest.raises(httpx.ConnectError):
        main.run("CLM-1002", "t", model)
    assert model.tried == main.MODEL_TRIES


def test_the_model_may_be_called_a_limited_number_of_times(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "CALL_LIMIT", 3)
    looping = StubModel(messages=looping_calls())
    with pytest.raises(AgentError, match="called 3 times"):
        main.run("CLM-1001", "t", looping)
    assert len([s for s in steps(client) if s["kind"] == "tool_call"]) == 3


def test_a_claim_that_fails_goes_back_to_waiting_and_starts_afresh(
    client: TestClient, agents: Agents, monkeypatch: pytest.MonkeyPatch
):
    saver = InMemorySaver()
    threads: list[str] = []
    models = iter(
        [
            StubModel(messages=looping_calls()),
            stub(calls(decision("decline", 0))),
        ]
    )
    monkeypatch.setattr(main, "CALL_LIMIT", 2)

    def runner(claim_id: str, thread_id: str) -> str:
        threads.append(thread_id)
        return main.run(claim_id, thread_id, next(models), saver)

    agents.claim(runner)
    assert client.post("/api/claims/CLM-1001/run").status_code == 202
    assert claim(client)["state"] == "waiting"
    assert steps(client)[-1]["kind"] == "error"
    assert client.post("/api/claims/CLM-1001/run").status_code == 202
    assert claim(client)["state"] == "decided"
    assert threads[0] != threads[1]
    assert len(claim(client)["decision"]["explanation"]) > 0


def test_instructions_come_from_the_backend(client: TestClient):
    model = RecordingModel(messages=iter([calls(decision("decline", 0))]))
    main.run("CLM-1002", "t", model)
    assert model.seen[0][0] == client.get("/api/instructions/claim").json()["text"]


def test_the_instructions_do_not_tell_the_agent_to_pay(client: TestClient):
    text = client.get("/api/instructions/claim").json()["text"]
    assert "pay_claim" not in text
    assert "calculate_payout" in text


def test_the_payout_tool_result_reaches_the_model(client: TestClient):
    line = {
        "description": "Hotel",
        "amount": 420,
        "quantity": 1,
        "kind": "expense",
        "nights": 0,
        "hours": 0,
    }
    model = RecordingModel(
        messages=iter(
            [
                calls(
                    (
                        "calculate_payout",
                        {"benefit": "trip_delay", "lines": [line], "paid_by_others": 0},
                    )
                ),
                calls(decision("approve", 420)),
            ]
        )
    )
    main.run("CLM-1001", "t", model)
    assert any('"payout":420.0' in text for text in model.seen[1])


def test_long_results_are_clipped_in_the_steps(client: TestClient):
    main.run(
        "CLM-1001",
        "thread-5",
        stub(
            calls(("get_document_section", {"document": "manual", "number": 7})),
            calls(decision("decline", 0)),
        ),
    )
    result = steps(client)[2]["text"]
    assert len(result) == main.STEP_LIMIT + 3
    assert result.endswith("...")


def test_model_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "deepseek/deepseek-v4.1-flash"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"


def test_agent_reaches_data_only_through_the_api():
    for path in Path(main.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] != "backend" for a in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "backend", path
        source = path.read_text()
        for forbidden in ("open(", "read_text", "data/", "sqlite3", "json.load("):
            assert forbidden not in source, (path, forbidden)
    assert json.loads(main.call("GET", "/api/health")) == {"status": "ok"}


def test_sections_read_are_recorded_in_order(client: TestClient):
    main.run(
        "CLM-1001",
        "thread-6",
        stub(
            calls(("list_document_sections", {"document": "wording"})),
            calls(("get_document_section", {"document": "wording", "number": 3})),
            calls(("get_document_section", {"document": "manual", "number": 10})),
            calls(("get_document_section", {"document": "manual", "number": 99})),
            calls(decision("decline", 0)),
        ),
    )
    read = [s["text"] for s in steps(client) if s["kind"] == "read"]
    assert read == [
        "Policy wording, 3. Trip Delay",
        "Claims manual, 10. Meals without receipts",
    ]
    assert any("Error 404" in s["text"] for s in steps(client))


def test_a_tool_that_fails_comes_back_to_the_model_as_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    real = main.call

    def flaky(method: str, path: str, body: dict[str, object] | None = None) -> str:
        if method == "GET" and path == "/api/claims/CLM-1001/claim-history":
            raise httpx.ReadTimeout("slow")
        return real(method, path, body)

    monkeypatch.setattr(main, "call", flaky)
    main.run(
        "CLM-1001",
        "thread-7",
        stub(calls(("get_claim_history", {})), calls(decision("decline", 0))),
    )
    assert "ReadTimeout" in steps(client)[1]["text"]


def test_a_payment_the_backend_refuses_stops_the_claim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    real = main.call

    def refusing(method: str, path: str, *args: Any, **kwargs: Any) -> str:
        if path.endswith("/payment"):
            return "Error 409: Payment blocked"
        return real(method, path, *args, **kwargs)

    monkeypatch.setattr(main, "call", refusing)
    with pytest.raises(AgentError, match="the payment was refused"):
        main.run("CLM-1001", "t", stub(calls(decision("approve", 420))))
    assert payments(client) is None
