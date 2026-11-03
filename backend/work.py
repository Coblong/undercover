"""Doing the claim agent's work: running it and ending the attempt."""

import logging
import threading
import time
from contextlib import closing

from agent.settings import error_text
from backend import attempts
from backend.agent_service import (
    UNREACHABLE,
    AgentStopped,
    AgentUnreachable,
    ClaimAgent,
)
from backend.db import connect
from backend.models import State

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_working_claims: set[str] = set()


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
        error = error_text(exc)
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
