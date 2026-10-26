"""The queue agent, which works the waiting claims from a to-do list."""

import json
from typing import Any, Literal, TypedDict

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool

from agent import main

CALL_LIMIT = 300
WORK_TIMEOUT = 900
REMINDERS = 2


class TodoItem(TypedDict):
    text: str
    status: Literal["to_do", "in_progress", "done", "left_for_handler"]


def queue_tools(run_id: int) -> list[BaseTool]:
    """Build the queue agent's tools for one run."""

    @tool
    def list_waiting_claims() -> str:
        """List the claims that are waiting, with their type, summary and amount."""
        claims = json.loads(main.call("GET", "/api/claims"))
        keys = ("claim_id", "claim_type", "summary", "amount_claimed", "submitted_on")
        return json.dumps(
            [{k: c[k] for k in keys} for c in claims if c["state"] == "waiting"]
        )

    @tool
    def write_todos(todos: list[TodoItem]) -> str:
        """Replace the to-do list with every item and its current status."""
        answer = main.call("PUT", f"/api/runs/{run_id}/todos", {"todos": todos})
        return answer if answer.startswith("Error") else f"Saved {len(todos)} items."

    @tool
    def work_claim(claim_id: str) -> str:
        """Hand a claim to a fresh helper to work, and get one line back."""
        path = f"/api/runs/{run_id}/claims/{claim_id}/work"
        try:
            answer = main.call("POST", path, timeout=WORK_TIMEOUT)
        except httpx.TimeoutException:
            return f"Error: no answer for {claim_id}. Check list_waiting_claims."
        return answer if answer.startswith("Error") else json.loads(answer)["message"]

    @tool
    def finish_run(summary: str) -> str:
        """Finish the run with a short summary for the claims handlers."""
        answer = main.call("POST", f"/api/runs/{run_id}/finish", {"summary": summary})
        return answer if answer.startswith("Error") else "The run is finished."

    return [list_waiting_claims, write_todos, work_claim, finish_run]


def run_queue(run_id: int, llm: BaseChatModel | None = None) -> None:
    """Work the queue for one run, reminding the agent to finish if it stops early."""
    instructions = json.loads(main.call("GET", "/api/instructions/queue"))["text"]
    limit = json.loads(main.call("GET", "/api/runs/latest"))["limit"]
    tools = queue_tools(run_id)
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        ModelRetryMiddleware(
            initial_delay=main.RETRY_DELAY, jitter=False, on_failure="error"
        ),
        ToolRetryMiddleware(
            tools=[t for t in tools if t.name != "work_claim"],
            initial_delay=main.RETRY_DELAY,
            jitter=False,
        ),
        ModelCallLimitMiddleware(run_limit=CALL_LIMIT),
    ]
    agent = create_agent(
        llm or main.model(),
        tools=tools,
        system_prompt=instructions,
        middleware=middleware,
    )
    messages: list[Any] = [
        {
            "role": "user",
            "content": f"Run the queue. You may start up to {limit} claims.",
        }
    ]
    for _ in range(1 + REMINDERS):
        messages = agent.invoke({"messages": messages})["messages"]
        if json.loads(main.call("GET", "/api/runs/latest"))["state"] != "running":
            return
        messages.append(
            {
                "role": "user",
                "content": "The run is not finished. Work any claims you have not"
                " worked, then call finish_run.",
            }
        )
    raise RuntimeError(f"the agent did not finish the run after {REMINDERS} reminders")
