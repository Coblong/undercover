import ast
import itertools
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import main
from agent.tests.stubs import StubModel, calls, decision, stub
from backend import main as backend_main

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


def test_tools_return_the_reason_when_the_backend_refuses():
    assert tools_for("CLM-9999")["get_claim"].invoke({}).startswith("Error 404")
    assert tools_for("CLM-9999")["get_policy"].invoke({}).startswith("Error 404")
    paid = tools_for()["pay_claim"].invoke({"amount": 10})
    assert paid.startswith("Error 409")
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
    tools["pay_claim"].invoke({"amount": 1})
    own = [p for p in paths if "CLM-1002" in p]
    other = [p for p in paths if p not in own]
    assert len(own) == 5
    assert other == ["/api/policies/POL-2002"] or all("POL-" in p for p in other)


def test_run_approves_and_pays(client: TestClient):
    reply = main.run(
        "CLM-1001",
        "thread-1",
        stub(
            calls(("get_claim", {})),
            calls(decision("approve", 420)),
            calls(("pay_claim", {"amount": 420})),
            AIMessage(content="Approved, $420.00."),
        ),
    )
    assert reply == "Approved, $420.00."
    done = claim(client)
    assert done["state"] == "decided"
    assert done["payment"]["amount"] == 420.0
    assert [s["kind"] for s in steps(client)] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "message",
    ]
    assert steps(client)[0]["text"] == "get_claim()"
    assert steps(client)[-1]["text"] == "Approved, $420.00."


def test_run_declines(client: TestClient):
    main.run(
        "CLM-1002",
        "thread-2",
        stub(calls(decision("decline", 0)), AIMessage(content="Declined.")),
    )
    done = claim(client, "CLM-1002")
    assert done["state"] == "decided"
    assert done["decision"]["decision"] == "decline"
    assert done["payment"] is None


def test_refused_payment_comes_back_to_the_agent(client: TestClient):
    main.run(
        "CLM-1003",
        "thread-3",
        stub(
            calls(decision("approve", 1250)),
            calls(("pay_claim", {"amount": 1250})),
            AIMessage(content="Approved $1,250.00. Payment waits for sign-off."),
        ),
    )
    done = claim(client, "CLM-1003")
    assert done["state"] == "waiting_for_sign_off"
    assert done["payment"] is None
    assert "Error 409: Payment blocked" in steps(client, "CLM-1003")[-2]["text"]


def test_instructions_come_from_the_backend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    seen: dict[str, object] = {}
    real = main.create_agent

    def spy(llm, **kwargs):
        seen.update(kwargs)
        return real(llm, **kwargs)

    monkeypatch.setattr(main, "create_agent", spy)
    main.run("CLM-1001", "thread-4", stub(AIMessage(content="Nothing to do.")))
    assert seen["system_prompt"] == client.get("/api/instructions/claim").json()["text"]


def test_long_results_are_clipped_in_the_steps(client: TestClient):
    main.run(
        "CLM-1001",
        "thread-5",
        stub(
            calls(("get_document_section", {"document": "manual", "number": 7})),
            AIMessage(content="Read it."),
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
            AIMessage(content="Read them."),
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
        if method == "GET" and path == "/api/claims/CLM-1001":
            raise httpx.ReadTimeout("slow")
        return real(method, path, body)

    monkeypatch.setattr(main, "call", flaky)
    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    reply = main.run(
        "CLM-1001",
        "thread-7",
        stub(calls(("get_claim", {})), AIMessage(content="Slow.")),
    )
    assert reply == "Slow."
    assert "ReadTimeout" in steps(client)[1]["text"]


def test_a_claim_that_loops_is_stopped_and_goes_back_to_waiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(main, "CALL_LIMIT", 3)
    looping = StubModel(messages=itertools.repeat(calls(("get_claim", {}))))
    monkeypatch.setattr(
        backend_main,
        "run_claim_agent",
        lambda claim_id, thread_id: main.run(claim_id, thread_id, looping),
    )
    assert client.post("/api/claims/CLM-1001/run").status_code == 202
    assert claim(client)["state"] == "waiting"
    assert steps(client)[-1]["kind"] == "error"
    assert len([s for s in steps(client) if s["kind"] == "tool_call"]) == 3
