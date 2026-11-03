"""The backend's one place that talks to the agent's service."""

from collections.abc import Callable

import httpx
from fastapi import HTTPException

from agent import settings
from agent.settings import AgentError

WORK_TIMEOUT = 900
START_TIMEOUT = 60
UNREACHABLE = "The agent's service could not be reached."

ClaimAgent = Callable[[str, str], str]
DispatchStarter = Callable[[int], int]
EvaluationStarter = Callable[[int, str, bool], int]


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


def start(path: str, body: dict[str, object]) -> int:
    """Ask the service to start a run and return the id it created.

    A service that is not running is a 503, and a refusal, such as a run already
    going, comes back with the service's own status and reason.
    """
    try:
        response = httpx.post(
            f"{settings.agent_url()}{path}", json=body, timeout=START_TIMEOUT
        )
    except httpx.TransportError as exc:
        raise HTTPException(503, UNREACHABLE) from exc
    if response.is_success:
        return response.json()["run_id"]
    try:
        reason = str(response.json()["detail"])
    except (ValueError, KeyError, TypeError):
        reason = "The agent's service could not start the run."
    status = 409 if response.status_code == httpx.codes.CONFLICT else 502
    raise HTTPException(status, reason)


def start_dispatch(concurrency: int) -> int:
    """Ask the service to start a dispatch with this many claim agents at once."""
    return start("/dispatches", {"concurrency": concurrency})


def dispatch_starter() -> DispatchStarter:
    """Return the function that starts a dispatch. Tests replace it."""
    return start_dispatch


def start_evaluation(concurrency: int, eval_set: str, tiny: bool) -> int:
    """Ask the service to start an evaluation of a labelled set."""
    body = {"concurrency": concurrency, "eval_set": eval_set, "tiny": tiny}
    return start("/evaluations", body)


def evaluation_starter() -> EvaluationStarter:
    """Return the function that starts an evaluation. Tests replace it."""
    return start_evaluation
