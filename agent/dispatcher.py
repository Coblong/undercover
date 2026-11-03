"""The dispatcher: a LangGraph graph with no model that works a run's claims at once.

It lists the claims, sends each to a worker, and sends the claims that went back to
the queue once more. It keeps no progress of its own: each claim saves its own on
its own thread, and the next dispatch finishes what a stopped one left.
"""

import json
import logging
import operator
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy, Send

from agent import main
from agent.settings import AgentError

logger = logging.getLogger(__name__)

WorkClaim = Callable[[str, int], str]

ROUNDS = 2
RETRY = RetryPolicy(
    max_attempts=4,
    initial_interval=5.0,
    backoff_factor=2.0,
    max_interval=60.0,
    retry_on=(ModelRateLimitError, ModelTimeoutError),
)


class Dispatch(TypedDict):
    """What the dispatcher keeps for one run."""

    run_id: int
    claims: list[str]
    round: int
    results: Annotated[list[dict[str, Any]], operator.add]


class Task(TypedDict):
    """What a worker is sent for one claim."""

    run_id: int
    claim_id: str
    round: int


Dispatcher = CompiledStateGraph[Dispatch, None, Dispatch, Dispatch]


def build(work: WorkClaim, retry: RetryPolicy = RETRY) -> Dispatcher:
    """Build the dispatcher around the one function that works a claim."""

    def list_claims(state: Dispatch) -> dict[str, Any]:
        """Ask the backend which claims the run works."""
        answer = main.call("POST", f"/api/runs/{state['run_id']}/plan")
        if answer.startswith("Error"):
            raise AgentError(answer)
        return {"claims": json.loads(answer)["claim_ids"], "round": 1}

    def fan_out(state: Dispatch) -> list[Send]:
        """Send each claim to a worker, in order."""
        return [
            Send(
                "work_claim",
                {"run_id": state["run_id"], "claim_id": c, "round": state["round"]},
            )
            for c in state["claims"]
        ]

    def work_claim(task: Task) -> dict[str, Any]:
        """Work one claim and say where it ended."""
        state = work(task["claim_id"], task["run_id"])
        return {"results": [{**task, "state": state}]}

    def stopped(task: Task, error: NodeError) -> dict[str, Any]:
        """Leave the claim as it is when the retries run out, so nothing stops."""
        logger.warning(
            "Left %s for the next dispatch: %s", task["claim_id"], repr(error.error)
        )
        return {"results": [{**task, "state": "stopped"}]}

    def check_again(state: Dispatch) -> dict[str, Any]:
        """Pick the claims that went back to the queue in this round."""
        again = [
            r["claim_id"]
            for r in state["results"]
            if r["round"] == state["round"] and r["state"] == "waiting"
        ]
        return {"claims": again, "round": state["round"] + 1}

    def after_check(state: Dispatch) -> list[Send] | str:
        """Work those claims once more, or end."""
        return fan_out(state) if state["claims"] and state["round"] <= ROUNDS else END

    builder = StateGraph(Dispatch)
    builder.add_node("list_claims", list_claims)
    builder.add_node(  # pyright: ignore[reportCallIssue]
        "work_claim",
        work_claim,  # pyright: ignore[reportArgumentType]
        input_schema=Task,
        retry_policy=retry,
        error_handler=stopped,  # pyright: ignore[reportArgumentType]
    )
    builder.add_node("check_again", check_again)
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges("list_claims", fan_out, ["work_claim"])
    builder.add_edge("work_claim", "check_again")
    builder.add_conditional_edges("check_again", after_check, ["work_claim", END])
    return builder.compile()


def run_dispatch(
    run_id: int, concurrency: int, work: WorkClaim, retry: RetryPolicy = RETRY
) -> None:
    """Work a run's claims, up to `concurrency` at once."""
    build(work, retry).invoke(
        {"run_id": run_id, "claims": [], "round": 1, "results": []},
        {"max_concurrency": concurrency},
    )
