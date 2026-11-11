import ast
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from agent import improver, improver_client, main, service
from agent.improver import RoundStopped
from agent.settings import AgentError
from agent.tests.stubs import StubModel, calls
from backend import judge

pytestmark = pytest.mark.usefixtures("in_process_backend")

BASE = "/api/improvement/rounds"


def change(**changes: Any) -> tuple[str, dict[str, Any]]:
    args = {
        "instructions": "Decide the claim.\nNever approve what the wording declines.",
        "model": "openai/gpt-6-luna",
        "reasoning_effort": "low",
        "reason": "Approvals of declined claims need a rule.",
        **changes,
    }
    return ("propose_change", args)


def new_round(client: TestClient) -> None:
    assert client.post(BASE, json={"handler_id": "HND-01"}).status_code == 201


class Recording(StubModel):
    """A stub model that keeps the messages it was given."""

    seen: list[list[Any]] = Field(default_factory=list)

    def _generate(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def scripted(*replies: AIMessage) -> Recording:
    return Recording(messages=iter(replies))


def build(model: Recording, evaluate=lambda _: "finished") -> Any:
    return improver.build(1, "1. Read.", model, InMemorySaver(), evaluate)


CONFIG = {"configurable": {"thread_id": "round-1-test"}}
GO = {"messages": [{"role": "user", "content": improver.START}]}
APPROVE = Command(resume={"decisions": [{"type": "approve"}]})


def test_the_improver_has_the_labs_four_tools_and_its_recipe_as_instructions(
    client: TestClient,
):
    new_round(client)
    tools = improver.improver_tools(1, lambda _: "finished")
    assert [t.name for t in tools] == [
        "read_results",
        "propose_change",
        "run_evaluation",
        "propose_recipe",
    ]
    text = improver.instructions("1. Read the results.")
    assert text.endswith("# Your recipe\n\n1. Read the results.\n")
    assert "never names a claim" in text
    assert "{approval_limit}" in text


def test_the_improver_runs_with_parallel_tool_calls_off(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    model = improver.improver_model()
    assert model.model_kwargs == {"parallel_tool_calls": False}
    assert (model.model_name, model.reasoning_effort) == ("openai/gpt-6-luna", "medium")
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    free = improver.improver_model()
    assert (free.model_name, free.reasoning_effort) == ("openrouter/free", None)


def test_read_results_gives_the_improver_its_view(client: TestClient):
    new_round(client)
    report = improver.improver_tools(1, lambda _: "finished")[0].invoke({})
    assert '"current_version"' in report
    assert '"models_allowed"' in report
    assert "CLM-11" not in report


def test_a_proposal_pauses_the_improver_for_a_handler(client: TestClient):
    new_round(client)
    model = scripted(calls(change()), AIMessage(content="Done."))
    graph = build(model)
    result = graph.invoke(GO, CONFIG)
    request = result["__interrupt__"][0].value["action_requests"][0]
    assert request["name"] == "propose_change"
    proposal = improver.proposal_of(request)
    assert proposal["kind"] == "change"
    assert proposal["model"] == "openai/gpt-6-luna"
    assert proposal["recipe"] is None
    # Nothing was saved: the tool has not run.
    assert len(client.get("/api/configurations").json()) == 1


def test_a_proposal_outside_the_models_allowed_goes_back_without_reaching_a_handler(
    client: TestClient,
):
    new_round(client)
    model = scripted(
        calls(change(model="openai/gpt-9")),
        calls(change(reasoning_effort="extreme")),
        calls(change()),
    )
    result = build(model).invoke(GO, CONFIG)
    request = result["__interrupt__"][0].value["action_requests"][0]
    assert request["args"]["model"] == "openai/gpt-6-luna"
    assert request["args"]["reasoning_effort"] == "low"
    told = [m.content for m in model.seen[1] if m.type == "tool"]
    assert told == [
        "openai/gpt-9 at low isn't allowed. Choose from the models allowed."
    ]
    told = [m.content for m in model.seen[2] if m.type == "tool"]
    assert told[-1].startswith("openai/gpt-6-luna at extreme isn't allowed")


def test_a_rejections_reason_reaches_the_improver_which_may_propose_again(
    client: TestClient,
):
    new_round(client)
    model = scripted(
        calls(change()),
        calls(change(instructions="A shorter rule.")),
        AIMessage(content="Done."),
    )
    graph = build(model)
    graph.invoke(GO, CONFIG)
    reject = Command(
        resume={"decisions": [{"type": "reject", "message": "Too specific."}]}
    )
    result = graph.invoke(reject, CONFIG)
    seen = " ".join(str(m.content) for m in model.seen[1])
    assert "Too specific." in seen
    request = result["__interrupt__"][0].value["action_requests"][0]
    assert request["args"]["instructions"] == "A shorter rule."


def test_the_improvers_model_calls_are_recorded_with_the_round(client: TestClient):
    new_round(client)
    reply = AIMessage(
        content="Done.",
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 50,
            "total_tokens": 1050,
        },
        response_metadata={"token_usage": {"cost": 0.0123}},
    )
    build(scripted(reply)).invoke(GO, CONFIG)
    round_ = client.get(f"{BASE}/1").json()
    assert round_["improver_cost"] == pytest.approx(0.0123)
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        found = conn.execute(
            "SELECT input_tokens, output_tokens FROM round_calls"
        ).fetchall()
    assert found == [(1000, 50)]


def test_a_stopped_round_makes_no_more_model_calls(client: TestClient):
    new_round(client)
    client.post("/api/rounds/1/stop")
    model = scripted(AIMessage(content="Done."))
    with pytest.raises(RoundStopped):
        build(model).invoke(GO, CONFIG)
    assert model.seen == []


def test_the_improvers_calls_are_capped_for_each_round(client: TestClient):
    new_round(client)
    # A rejected proposal each time keeps the improver asking again.
    replies = [calls(change()) for _ in range(improver.CALL_LIMIT + 5)]
    graph = build(scripted(*replies))
    result = graph.invoke(GO, CONFIG)
    reject = Command(resume={"decisions": [{"type": "reject", "message": "No."}]})
    rounds = 0
    while "__interrupt__" in result and rounds < improver.CALL_LIMIT + 5:
        result = graph.invoke(reject, CONFIG)
        rounds += 1
    assert "__interrupt__" not in result
    assert rounds <= improver.CALL_LIMIT


def test_the_evaluation_tool_runs_three_evaluations_and_asks_for_the_verdict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    new_round(client)
    client.post(f"{BASE}/1/proposal", json={**proposal(), "kind": "change"})
    client.post(
        f"{BASE}/1/decision",
        json={"outcome": "approved", "handler_id": "HND-01", "reason": None},
    )
    ran: list[str] = []

    def evaluate(eval_set: str) -> str:
        ran.append(eval_set)
        return "finished"

    run_evaluation = improver.improver_tools(1, evaluate)[2]
    answer = run_evaluation.invoke({})
    assert ran == ["evaluation", "held_out", "held_out"]
    # The stub evaluator ran nothing, so there is nothing to compare.
    assert answer.startswith(
        "Rolled back version 2: the current version has no held-out scores"
    )
    round_ = client.get(f"{BASE}/1").json()
    assert round_["verdict"] == "rolled_back"
    assert run_evaluation.invoke({}).startswith("There's no new version to evaluate")


def test_an_evaluation_that_was_stopped_rolls_the_version_back(client: TestClient):
    new_round(client)
    client.post(f"{BASE}/1/proposal", json=proposal())
    client.post(
        f"{BASE}/1/decision",
        json={"outcome": "approved", "handler_id": "HND-01", "reason": None},
    )
    tool = improver.improver_tools(1, lambda _: "stopped")[2]
    assert "An evaluation was stopped" in tool.invoke({})
    assert client.get(f"{BASE}/1").json()["why"] == "its evaluations were stopped"


def test_an_evaluation_the_round_cannot_afford_is_refused_and_rolls_the_version_back(
    client: TestClient,
):
    values = client.get("/api/settings").json()["values"]
    client.put("/api/settings", json={**values, "spending_limit": 0.01})
    new_round(client)
    client.post(f"{BASE}/1/proposal", json=proposal())
    client.post(
        f"{BASE}/1/decision",
        json={"outcome": "approved", "handler_id": "HND-01", "reason": None},
    )
    client.post(
        f"{BASE}/1/calls", json={"input_tokens": 1, "output_tokens": 1, "cost": 0.02}
    )
    ran: list[str] = []

    def evaluate(eval_set: str) -> str:
        ran.append(eval_set)
        return "finished"

    tool = improver.improver_tools(1, evaluate)[2]
    assert tool.invoke({}).startswith("Refused: the evaluation could cost")
    assert ran == []
    assert client.get(f"{BASE}/1").json()["why"] == improver.OUT_OF_MONEY


def proposal() -> dict[str, Any]:
    _, args = change()
    return {"kind": "change", "recipe": None, **args}


def test_the_improvers_client_has_no_way_to_a_claims_own_routes():
    tree = ast.parse(Path(improver_client.__file__).read_text())
    paths = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/api/")
    ]
    assert paths
    for path in paths:
        assert "/claims" not in path.replace("/api/improvement", ""), path
        assert not path.startswith("/api/claims"), path


def test_the_improvers_client_says_when_a_round_was_stopped(client: TestClient):
    new_round(client)
    client.post("/api/rounds/1/stop")
    with pytest.raises(improver_client.Refused) as refused:
        improver_client.record_stage(1, "evaluating", 1, 3)
    assert refused.value.round_stopped is True
    with pytest.raises(improver_client.Refused) as other:
        improver_client.get_round(99)
    assert other.value.round_stopped is False
    assert improver_client.recipe_text(1).startswith("1. Call read_results")
    with pytest.raises(Exception, match="Recipe version 7"):
        improver_client.recipe_text(7)


# --- the service's rounds ------------------------------------------------


def until(check, seconds: float = 15.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("it never happened")


@pytest.fixture
def agent_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        judge, "grade", lambda case: judge.Grade(1.5, "B", True, False, 0.0001)
    )
    service.app.state.checkpointer = InMemorySaver()
    with TestClient(service.app, raise_server_exceptions=False) as running:
        yield running
    service.app.state.checkpointer = None
    service.app.dependency_overrides.clear()
    if service._gate.locked():  # noqa: SLF001
        service._gate.release()  # noqa: SLF001


class Worker:
    """A stand-in claim worker: it refers every claim it is given, with a grade."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.worked: list[tuple[str, int]] = []
        self.gate = threading.Event()
        self.gate.set()
        self.waiting = 0

    def __call__(self, claim_id: str, run_id: int) -> str:
        with self.lock:
            self.waiting += 1
        self.gate.wait(10)
        started = main.call("POST", f"/api/runs/{run_id}/claims/{claim_id}/start")
        if started.startswith("Error"):
            raise AgentError(started)
        with self.lock:
            self.worked.append((claim_id, run_id))
        body = {
            "decision": "refer",
            "payout": 0,
            "explanation": "grade=1.5",
            "policy_section": "3. Trip Delay",
        }
        main.call("POST", f"/api/claims/{claim_id}/decision", body)
        main.call("POST", f"/api/claims/{claim_id}/attempt/end", {"error": None})
        return "decided"


@pytest.fixture
def worker() -> Worker:
    stand_in = Worker()
    service.app.dependency_overrides[service.claim_worker] = lambda: stand_in
    return stand_in


def use_improver(*replies: AIMessage) -> Recording:
    model = scripted(*replies)
    service.app.dependency_overrides[service.improver_llm] = lambda: model
    return model


def stage(client: TestClient) -> str:
    return client.get(f"{BASE}/1").json()["stage"]


def start(agent_service: TestClient, handler: str = "HND-01"):
    return agent_service.post(
        "/rounds", json={"handler_id": handler, "handler_name": "Dana Whitfield"}
    )


def decide(agent_service: TestClient, outcome: str, reason: str | None = None):
    return agent_service.post(
        "/rounds/1/decision",
        json={"outcome": outcome, "handler_id": "HND-01", "reason": reason},
    )


def test_a_round_runs_the_first_evaluations_then_waits_for_a_handler(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    use_improver(calls(("read_results", {})), calls(change()))
    assert start(agent_service).json() == {"run_id": 1}
    until(lambda: stage(client) == "change_waiting")
    round_ = client.get(f"{BASE}/1").json()
    # One evaluation of the evaluation set and two of the held-out set.
    evaluations = client.get("/api/evaluations").json()
    assert sorted(e["eval_set"] for e in evaluations) == [
        "evaluation",
        "held_out",
        "held_out",
    ]
    assert all(
        e["state"] == "finished" and e["config_version"] == 1 for e in evaluations
    )
    assert round_["needs"] == {"evaluation": 0, "held_out": 0}
    assert round_["pending"]["kind"] == "change"
    assert round_["started_by"] == "Dana Whitfield"
    assert len(client.get("/api/configurations").json()) == 1
    # The improver read the results, then proposed.
    until(lambda: not service._gate.locked())  # noqa: SLF001


def test_a_waiting_round_lets_a_dispatch_run_and_the_approval_waits_for_it(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    use_improver(calls(change()))
    start(agent_service)
    until(lambda: stage(client) == "change_waiting")
    until(lambda: not service._gate.locked())  # noqa: SLF001

    worker.gate.clear()
    dispatched = agent_service.post("/dispatches", json={"concurrency": 3})
    assert dispatched.status_code == 202
    refused = decide(agent_service, "approved")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "A dispatch is already running"
    after = client.get(f"{BASE}/1").json()
    assert after["decisions"] == []
    assert after["pending"] is not None
    assert len(client.get("/api/configurations").json()) == 1
    worker.gate.set()
    until(lambda: not service._gate.locked())  # noqa: SLF001


def test_an_approved_change_is_evaluated_and_the_improver_goes_on_to_a_recipe(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    model = use_improver(
        calls(change()),
        calls(("run_evaluation", {})),
        calls(
            (
                "propose_recipe",
                {"recipe": "1. Read.\n2. Propose.", "reason": "Shorter."},
            )
        ),
        AIMessage(content="Finished."),
    )
    start(agent_service)
    until(lambda: stage(client) == "change_waiting")
    until(lambda: not service._gate.locked())  # noqa: SLF001

    assert decide(agent_service, "approved").status_code == 202
    until(lambda: stage(client) == "recipe_waiting")
    round_ = client.get(f"{BASE}/1").json()
    assert round_["tried_version"] == 2
    assert round_["verdict"] in ("kept", "rolled_back")
    assert round_["decisions"][0]["handler"] == "Dana Whitfield"
    evaluations = client.get("/api/evaluations").json()
    assert sorted((e["config_version"], e["eval_set"]) for e in evaluations) == [
        (1, "evaluation"),
        (1, "held_out"),
        (1, "held_out"),
        (2, "evaluation"),
        (2, "held_out"),
        (2, "held_out"),
    ]
    assert round_["pending"]["kind"] == "recipe"
    until(lambda: not service._gate.locked())  # noqa: SLF001

    assert decide(agent_service, "approved").status_code == 202
    until(lambda: client.get(f"{BASE}/1").json()["state"] == "finished")
    assert len(client.get("/api/recipes").json()) == 2
    until(lambda: not service._gate.locked())  # noqa: SLF001
    assert any("saved as version 2" in str(m.content) for m in model.seen[1])


def test_a_rejection_carries_the_reason_back_and_the_improver_proposes_again(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    model = use_improver(calls(change()), calls(change(instructions="A shorter rule.")))
    start(agent_service)
    until(lambda: stage(client) == "change_waiting")
    until(lambda: not service._gate.locked())  # noqa: SLF001
    assert decide(agent_service, "rejected", "Too specific.").status_code == 202
    until(
        lambda: (
            client.get(f"{BASE}/1").json()["pending"] is not None
            and client.get(f"{BASE}/1").json()["decisions"]
        )
    )
    round_ = client.get(f"{BASE}/1").json()
    assert round_["decisions"][0]["outcome"] == "rejected"
    assert round_["pending"]["diff"]
    assert any("Too specific." in str(m.content) for m in model.seen[1])
    assert len(client.get("/api/configurations").json()) == 1
    until(lambda: not service._gate.locked())  # noqa: SLF001


def test_a_round_waiting_for_a_handler_survives_a_restart(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    use_improver(calls(change()), calls(("run_evaluation", {})), AIMessage(content="x"))
    start(agent_service)
    until(lambda: stage(client) == "change_waiting")
    until(lambda: not service._gate.locked())  # noqa: SLF001
    saver = service.app.state.checkpointer

    # A new service starts on the same saved progress and the same backend.
    with TestClient(service.app, raise_server_exceptions=False) as restarted:
        assert client.get(f"{BASE}/1").json()["state"] == "open"
        assert service.app.state.checkpointer is saver
        assert decide(restarted, "approved").status_code == 202
        until(lambda: client.get(f"{BASE}/1").json()["tried_version"] == 2)
        until(lambda: client.get(f"{BASE}/1").json()["verdict"] is not None)
        until(lambda: not service._gate.locked())  # noqa: SLF001


def test_starting_the_service_stops_a_round_that_was_mid_work(
    client: TestClient, agent_service: TestClient
):
    new_round(client)
    with TestClient(service.app):
        pass
    assert client.get(f"{BASE}/1").json()["state"] == "stopped"


def test_a_stopped_round_starts_no_more_claims_and_gives_the_gate_back(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    worker.gate.clear()
    use_improver(calls(change()))
    start(agent_service)
    until(lambda: worker.waiting == 25)
    assert client.post("/api/rounds/1/stop").status_code == 200
    worker.gate.set()
    until(lambda: not service._gate.locked())  # noqa: SLF001
    assert worker.worked == []
    round_ = client.get(f"{BASE}/1").json()
    assert round_["state"] == "stopped"
    assert client.get("/api/configuration/current").json()["version"] == 1
    [evaluation] = client.get("/api/evaluations").json()
    assert evaluation["state"] == "stopped"
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        attempts = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()
    assert attempts == (0,)


def test_a_second_round_is_refused_while_one_is_open(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    use_improver(calls(change()))
    start(agent_service)
    until(lambda: stage(client) == "change_waiting")
    until(lambda: not service._gate.locked())  # noqa: SLF001
    again = start(agent_service)
    assert again.status_code == 409
    assert "already open" in again.json()["detail"]
    assert not service._gate.locked()  # noqa: SLF001


def test_a_start_is_refused_while_the_improver_is_working(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    worker.gate.clear()
    use_improver(calls(change()))
    start(agent_service)
    until(service._gate.locked)  # noqa: SLF001
    refused = agent_service.post("/dispatches", json={"concurrency": 2})
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "The improver is already working",
    )
    worker.gate.set()
    until(lambda: stage(client) == "change_waiting")


def test_the_improvers_evaluations_are_part_of_its_own_work_and_never_refused(
    client: TestClient, agent_service: TestClient, worker: Worker
):
    use_improver(calls(change()))
    start(agent_service)
    until(lambda: stage(client) == "change_waiting")
    assert len(client.get("/api/evaluations").json()) == 3
    assert all(e["state"] == "finished" for e in client.get("/api/evaluations").json())
    until(lambda: not service._gate.locked())  # noqa: SLF001
