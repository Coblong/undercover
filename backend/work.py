"""Doing the agents' work: running them, ending the attempt and telling the story."""

import logging
import threading
import time
from collections.abc import Callable
from contextlib import closing

import httpx
from sqlalchemy import Connection, select

from agent import settings
from agent.queue import run_queue
from agent.settings import AgentError
from backend import attempts, queries, runs
from backend import tables as t
from backend.db import connect
from backend.decisions import money
from backend.models import State

logger = logging.getLogger(__name__)

ClaimAgent = Callable[[str, str], str]
QueueAgent = Callable[[int], None]

WORK_TIMEOUT = 900
UNREACHABLE = "The agent's service could not be reached."

_lock = threading.Lock()
_working_claims: set[str] = set()
_working_runs: set[int] = set()


class AgentStopped(Exception):
    """The agent's service went away while it worked, so the work was not ended."""


class AgentUnreachable(AgentStopped):
    """The agent's service could not be reached, so the work never started."""


def call_service(claim_id: str, thread_id: str) -> str:
    """Ask the agent's service to work a claim on a thread, and return its one line."""
    try:
        response = httpx.post(
            f"{settings.agent_url()}/claims/{claim_id}/work",
            json={"thread_id": thread_id},
            timeout=WORK_TIMEOUT,
        )
    except httpx.ConnectError as exc:
        raise AgentUnreachable(UNREACHABLE) from exc
    except httpx.TransportError as exc:
        raise AgentStopped(type(exc).__name__) from exc
    if not response.is_success:
        raise AgentError(response.text)
    return response.json()["reply"]


def claim_agent() -> ClaimAgent:
    """Return the agent that works one claim. Tests replace it."""
    return call_service


def queue_agent() -> QueueAgent:
    """Return the agent that works the queue. Tests replace it."""
    return run_queue


def enter[T](working: set[T], key: T) -> bool:
    """Note that a claim or run is being worked, unless it already is."""
    with _lock:
        if key in working:
            return False
        working.add(key)
        return True


def leave[T](working: set[T], key: T) -> None:
    """Note that a claim or run is no longer being worked."""
    with _lock:
        working.discard(key)


def is_working(claim_id: str) -> bool:
    """Say whether this process is working a claim now."""
    with _lock:
        return claim_id in _working_claims


def working_runs() -> frozenset[int]:
    """Return the runs this process is working now."""
    with _lock:
        return frozenset(_working_runs)


def step_text(who: str, exc: Exception) -> str:
    """Say in a step that something stopped, without leaking details into the page.

    The traceback goes to the log. The team sees the kind of error, and the
    message only when the agent itself wrote it for them.
    """
    if isinstance(exc, AgentError):
        return f"{who} stopped with an error: {exc}"
    return f"{who} stopped with an error ({type(exc).__name__})"


def execute_attempt(
    claim_id: str, run_id: int | None, agent: ClaimAgent, resume: bool = False
) -> State:
    """Run the claim agent on a claim that has been started, then end the attempt.

    With `resume` the claim has an open attempt whose thread the agent carries on.
    If the agent stops, the attempt stays open for the next run to carry on.
    """
    if not enter(_working_claims, claim_id):
        return "in_progress"
    try:
        return run_attempt(claim_id, run_id, agent, resume)
    finally:
        leave(_working_claims, claim_id)


def run_attempt(
    claim_id: str, run_id: int | None, agent: ClaimAgent, resume: bool
) -> State:
    """Run one attempt, and end it unless the agent stopped."""
    started = time.monotonic()
    error = None
    with closing(connect()) as conn:
        thread_id = attempts.open_thread(conn, claim_id)
    if thread_id is None:
        return "in_progress"
    try:
        agent(claim_id, thread_id)
    except AgentUnreachable as exc:
        logger.warning("Could not reach the agent for %s: %s", claim_id, exc)
        if resume:
            return "in_progress"
        with closing(connect()) as conn:
            attempts.release(conn, claim_id, UNREACHABLE)
        return "waiting"
    except AgentStopped as exc:
        logger.warning("The agent stopped while working %s: %s", claim_id, exc)
        return "in_progress"
    except Exception as exc:  # the run is in the background, so nobody else sees this
        logger.exception("The agent failed on %s", claim_id)
        error = step_text("The agent", exc)
    with closing(connect()) as conn:
        state = attempts.end_attempt(conn, claim_id, error)
    logger.info(
        "Attempt ended: claim=%s run=%s state=%s failed=%s seconds=%.1f",
        claim_id,
        run_id,
        state,
        error is not None or state in ("waiting", "left_for_handler"),
        time.monotonic() - started,
    )
    return state


def carry_on_stopped(agent: ClaimAgent) -> None:
    """Carry on every claim a stopped agent left in progress, one at a time."""
    with closing(connect()) as conn:
        stopped = attempts.stopped_claims(conn)
    for claim_id in stopped:
        execute_attempt(claim_id, None, agent, resume=True)


def execute_queue(run_id: int, agent: QueueAgent, claim: ClaimAgent) -> None:
    """Carry on stopped claims, run the queue agent, then close the run if going."""
    if not enter(_working_runs, run_id):
        return
    error = None
    try:
        carry_on_stopped(claim)
        agent(run_id)
    except Exception as exc:  # the run is in the background, so nobody else sees this
        logger.exception("The queue agent failed in run %s", run_id)
        error = step_text("The queue agent", exc)
    finally:
        leave(_working_runs, run_id)
    with closing(connect()) as conn:
        runs.close_if_running(conn, run_id, error)


def result_line(conn: Connection, claim_id: str) -> str:
    """Say in one line how the work on a claim ended, for the queue agent."""
    claim = queries.get_claim(conn, claim_id)
    decision = claim.decision
    if claim.state == "left_for_handler":
        return f"{claim_id} failed twice and is left for a handler."
    if claim.state == "in_progress":
        return f"{claim_id} was stopped partway. The next run carries it on."
    if claim.state == "waiting" or decision is None:
        problem = conn.execute(
            select(t.steps.c.text)
            .where(t.steps.c.claim_id == claim_id, t.steps.c.kind == "error")
            .order_by(t.steps.c.id.desc())
            .limit(1)
        ).scalar_one()
        return f"{claim_id} failed ({problem}) and is waiting to be tried again."
    line = f"{claim_id} decided: {decision.decision}"
    if decision.decision != "approve":
        return line + "."
    if claim.payment is not None:
        return f"{line} {money(decision.payout)}, paid."
    return f"{line} {money(decision.payout)}, waiting for a handler's sign-off."
