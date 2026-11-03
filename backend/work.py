"""Doing the claim agent's work: running it and ending the attempt."""

import logging
import threading
import time
from collections.abc import Callable
from contextlib import closing

import httpx

from agent import settings
from agent.settings import AgentError, error_text
from backend import attempts
from backend.db import connect
from backend.models import State

logger = logging.getLogger(__name__)

ClaimAgent = Callable[[str, str], str]

WORK_TIMEOUT = 900
UNREACHABLE = "The agent's service could not be reached."

_lock = threading.Lock()
_working_claims: set[str] = set()


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
    if response.status_code == httpx.codes.CONFLICT:
        raise AgentStopped("the claim is already being worked")
    if not response.is_success:
        raise AgentError(response.text)
    return response.json()["reply"]


def claim_agent() -> ClaimAgent:
    """Return the agent that works one claim. Tests replace it."""
    return call_service


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
