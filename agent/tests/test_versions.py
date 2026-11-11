import ast
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent import main
from agent.tests.stubs import StubModel, calls, decision, stub

pytestmark = pytest.mark.usefixtures("in_process_backend")

# What the claim agent's client must never reach: scores, the handlers' decisions,
# the improver and the settings.
FORBIDDEN = ("evaluations", "rounds", "settings", "configurations", "answer", "scores")


def sql(statement: str, *values: Any) -> None:
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        conn.execute(statement, values)


def add_version_2() -> None:
    sql(
        "INSERT INTO configurations (version, instructions, model, reasoning_effort,"
        " reason, proposed_by, created_at) VALUES (2, 'Version two instructions',"
        " 'google/gemini-3.5-flash-lite', 'minimal', 'r', 'Round 1', 'then')"
    )


def test_the_claim_agent_is_built_from_the_version_its_run_used(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run = client.post("/api/runs/dispatch", json={"concurrency": 1}).json()["run_id"]
    add_version_2()
    sql("UPDATE current_configuration SET version = 2")
    built: list[tuple[str, str]] = []
    seen: list[str] = []

    class Recording(StubModel):
        def invoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
            seen.append(messages[0].content)
            return super().invoke(messages, *args, **kwargs)

    def fake_model(name: str, effort: str) -> StubModel:
        built.append((name, effort))
        return Recording(messages=iter([calls(decision("decline", 0))]))

    monkeypatch.setattr(main, "model", fake_model)
    main.run("CLM-1002", "thread-run", None, None, run)
    assert built == [("openai/gpt-6-luna", "low")]
    assert "Version two" not in seen[0]

    main.run("CLM-1003", "thread-current", None, None, None)
    assert built[1] == ("google/gemini-3.5-flash-lite", "minimal")
    assert seen[1] == "Version two instructions"


def test_a_run_whose_configuration_cannot_be_read_stops_with_the_reason():
    stub_model = stub(calls(decision("decline", 0)))
    with pytest.raises(main.AgentError, match="Run 99 not found"):
        main.run("CLM-1002", "thread-x", stub_model, None, 99)


def test_the_claim_agents_client_has_no_way_to_the_scores_or_the_improver():
    tree = ast.parse(Path(main.__file__).read_text())
    paths = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/api/")
    ]
    assert paths
    for path in paths:
        assert not any(word in path for word in FORBIDDEN), path
