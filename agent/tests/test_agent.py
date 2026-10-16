import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent import main
from backend.main import app


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies in place of a real model."""

    def bind_tools(self, tools, **kwargs):
        return self


def calls(*calls: tuple[str, dict[str, object]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": str(i)}
            for i, (name, args) in enumerate(calls)
        ],
    )


def decision(claim_id: str, kind: str, payout: float) -> tuple[str, dict[str, object]]:
    args = {
        "claim_id": claim_id,
        "decision": kind,
        "payout": payout,
        "explanation": "Covered by the wording.",
        "policy_section": "3. Trip Delay",
    }
    return ("decide_claim", args)


def stub(*replies: AIMessage) -> StubModel:
    return StubModel(messages=iter(replies))


@pytest.fixture(autouse=True)
def in_process_backend(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))


def claim(client: TestClient, claim_id: str = "CLM-1001") -> dict[str, Any]:
    return client.get(f"/api/claims/{claim_id}").json()


def steps(client: TestClient, claim_id: str = "CLM-1001") -> list[dict[str, str]]:
    return client.get(f"/api/claims/{claim_id}/steps").json()


def test_read_tools():
    assert "Rome" in main.get_claim.invoke({"claim_id": "CLM-1001"})
    assert '"plan":"premium"' in main.get_policy.invoke({"policy_id": "POL-2001"})
    history = main.get_claim_history.invoke({"customer_id": "CUS-3001"})
    assert "CLM-0412" in history
    assert "email" not in history
    assert "Policy Wording" in main.get_policy_wording.invoke({})


def test_tools_return_the_reason_when_the_backend_refuses():
    assert main.get_claim.invoke({"claim_id": "CLM-9999"}).startswith("Error 404")
    paid = main.pay_claim.invoke({"claim_id": "CLM-1001", "amount": 10})
    assert paid.startswith("Error 409")


def test_run_approves_and_pays(client: TestClient):
    reply = main.run(
        "CLM-1001",
        "thread-1",
        stub(
            calls(("get_claim", {"claim_id": "CLM-1001"})),
            calls(decision("CLM-1001", "approve", 420)),
            calls(("pay_claim", {"claim_id": "CLM-1001", "amount": 420})),
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
    assert steps(client)[0]["text"] == 'get_claim(claim_id="CLM-1001")'
    assert steps(client)[-1]["text"] == "Approved, $420.00."


def test_run_declines(client: TestClient):
    main.run(
        "CLM-1002",
        "thread-2",
        stub(calls(decision("CLM-1002", "decline", 0)), AIMessage(content="Declined.")),
    )
    done = claim(client, "CLM-1002")
    assert done["state"] == "decided"
    assert done["decision"]["decision"] == "decline"
    assert done["payment"] is None


def test_refused_payment_comes_back_to_the_agent(client: TestClient):
    main.run(
        "CLM-1001",
        "thread-3",
        stub(
            calls(decision("CLM-1001", "approve", 1250)),
            calls(("pay_claim", {"claim_id": "CLM-1001", "amount": 1250})),
            AIMessage(content="Approved $1,250.00. Payment waits for sign-off."),
        ),
    )
    done = claim(client)
    assert done["state"] == "waiting_for_sign_off"
    assert done["payment"] is None
    assert "Error 409: Payment blocked" in steps(client)[-2]["text"]


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
    assert seen["system_prompt"] == client.get("/api/instructions").json()["text"]


def test_long_results_are_clipped_in_the_steps(client: TestClient):
    main.run(
        "CLM-1001",
        "thread-5",
        stub(calls(("get_policy_wording", {})), AIMessage(content="Read it.")),
    )
    result = steps(client)[1]["text"]
    assert len(result) == main.STEP_LIMIT + 3
    assert result.endswith("...")


def test_model_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "openai/gpt-6-luna"
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
