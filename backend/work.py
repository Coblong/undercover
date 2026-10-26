"""Doing the agents' work: running them, ending the attempt and telling the story."""

import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from uuid import uuid4

from agent.main import run as run_claim
from agent.queue import run_queue
from agent.settings import AgentError
from backend import queries, runs
from backend.attempts import end_attempt
from backend.db import connect
from backend.decisions import money
from backend.models import State

logger = logging.getLogger(__name__)

ClaimAgent = Callable[[str, str], str]
QueueAgent = Callable[[int], None]


def claim_agent() -> ClaimAgent:
    """Return the agent that works one claim. Tests replace it."""
    return run_claim


def queue_agent() -> QueueAgent:
    """Return the agent that works the queue. Tests replace it."""
    return run_queue


def step_text(who: str, exc: Exception) -> str:
    """Say in a step that something stopped, without leaking details into the page.

    The traceback goes to the log. The team sees the kind of error, and the
    message only when the agent itself wrote it for them.
    """
    if isinstance(exc, AgentError):
        return f"{who} stopped with an error: {exc}"
    return f"{who} stopped with an error ({type(exc).__name__})"


def execute_attempt(claim_id: str, run_id: int | None, agent: ClaimAgent) -> State:
    """Run the claim agent on a claim that has been started, then end the attempt."""
    started = time.monotonic()
    error = None
    try:
        agent(claim_id, f"{claim_id}-{uuid4().hex[:8]}")
    except Exception as exc:  # the run is in the background, so nobody else sees this
        logger.exception("The agent failed on %s", claim_id)
        error = step_text("The agent", exc)
    with closing(connect()) as conn:
        state = end_attempt(conn, claim_id, error)
    logger.info(
        "Attempt ended: claim=%s run=%s state=%s failed=%s seconds=%.1f",
        claim_id,
        run_id,
        state,
        error is not None or state in ("waiting", "left_for_handler"),
        time.monotonic() - started,
    )
    return state


def execute_queue(run_id: int, agent: QueueAgent) -> None:
    """Run the queue agent, then close the run if it left it going."""
    error = None
    try:
        agent(run_id)
    except Exception as exc:  # the run is in the background, so nobody else sees this
        logger.exception("The queue agent failed in run %s", run_id)
        error = step_text("The queue agent", exc)
    with closing(connect()) as conn:
        runs.close_if_running(conn, run_id, error)


def result_line(conn: sqlite3.Connection, claim_id: str) -> str:
    """Say in one line how the work on a claim ended, for the queue agent."""
    claim = queries.get_claim(conn, claim_id)
    decision = claim.decision
    if claim.state == "left_for_handler":
        return f"{claim_id} failed twice and is left for a handler."
    if claim.state == "waiting" or decision is None:
        problem = conn.execute(
            "SELECT text FROM steps WHERE claim_id = ? AND kind = 'error'"
            " ORDER BY id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        return (
            f"{claim_id} failed ({problem['text']}) and is waiting to be tried again."
        )
    line = f"{claim_id} decided: {decision.decision}"
    if decision.decision != "approve":
        return line + "."
    if claim.payment is not None:
        return f"{line} {money(decision.payout)}, paid."
    return f"{line} {money(decision.payout)}, waiting for a handler's sign-off."
