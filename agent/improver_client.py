"""The improver's client for the backend.

The claim agent's client (`main.call`) has no way to the scores, the handlers'
decisions or the rounds. This client has the way to the rounds and nothing to do
with a claim's own work, so each stays on its own side.
"""

from typing import Any

import httpx

from agent import settings
from agent.settings import AgentError

TIMEOUT = 60
BASE = "/api/improvement/rounds"
STOPPED = "was stopped"


class Refused(Exception):
    """The backend refused a call, with its reason."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason

    @property
    def round_stopped(self) -> bool:
        """True when the round was stopped, so nothing more may be recorded."""
        return self.status == httpx.codes.CONFLICT and STOPPED in self.reason


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=settings.backend_url(), timeout=TIMEOUT)


def request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Call the backend and return its answer, or raise the reason it refused."""
    try:
        with backend() as client:
            response = client.request(method, path, json=body)
    except httpx.TransportError as exc:
        raise AgentError(
            f"the backend could not be reached ({type(exc).__name__})"
        ) from exc
    if response.is_success:
        return response.json()
    try:
        reason = str(response.json()["detail"])
    except (ValueError, KeyError, TypeError):
        reason = response.text or response.reason_phrase
    raise Refused(response.status_code, reason)


def create_round(handler_id: str) -> dict[str, Any]:
    """Open a round for a handler."""
    return request("POST", BASE, {"handler_id": handler_id})


def get_round(round_id: int) -> dict[str, Any]:
    """Read a round."""
    return request("GET", f"{BASE}/{round_id}")


def record_stage(round_id: int, stage: str, done: int, of: int) -> None:
    """Say where the round has got to."""
    request(
        "POST", f"{BASE}/{round_id}/stage", {"stage": stage, "done": done, "of": of}
    )


def record_proposal(round_id: int, proposal: dict[str, Any]) -> None:
    """Record the proposal the round waits on."""
    request("POST", f"{BASE}/{round_id}/proposal", proposal)


def record_decision(
    round_id: int, outcome: str, handler_id: str, reason: str | None
) -> dict[str, Any]:
    """Record a handler's decision on the proposal."""
    body = {"outcome": outcome, "handler_id": handler_id, "reason": reason}
    return request("POST", f"{BASE}/{round_id}/decision", body)


def record_call(
    round_id: int, input_tokens: int, output_tokens: int, cost: float
) -> None:
    """Record one of the improver's model calls."""
    body = {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost": cost}
    request("POST", f"{BASE}/{round_id}/calls", body)


def improver_view(round_id: int) -> dict[str, Any]:
    """Read what the improver is shown."""
    return request("GET", f"{BASE}/{round_id}/view")["report"]


def check_evaluation(round_id: int, eval_set: str) -> dict[str, Any]:
    """Ask whether the round can afford an evaluation of a whole set."""
    return request("POST", f"{BASE}/{round_id}/check", {"eval_set": eval_set})


def give_verdict(round_id: int, reason: str | None) -> dict[str, Any]:
    """Have the backend keep or roll back the version the round tried."""
    return request("POST", f"{BASE}/{round_id}/verdict", {"reason": reason})


def end_round(round_id: int, state: str, why: str | None) -> dict[str, Any]:
    """End the round."""
    return request("POST", f"{BASE}/{round_id}/end", {"state": state, "why": why})


def models_allowed() -> list[dict[str, Any]]:
    """Read the models the claim agent may use."""
    return request("GET", "/api/settings")["models"]


def recipe_text(version: int) -> str:
    """Read one version of the recipe."""
    for recipe in request("GET", "/api/recipes"):
        if recipe["version"] == version:
            return recipe["recipe"]
    raise AgentError(f"Recipe version {version} not found")


def create_evaluation(round_id: int, eval_set: str, concurrency: int) -> int:
    """Create a whole-set evaluation for the round and return its run id."""
    body = {
        "concurrency": concurrency,
        "eval_set": eval_set,
        "tiny": False,
        "round_id": round_id,
    }
    return request("POST", "/api/runs/evaluation", body)["run_id"]


def end_run(run_id: int, state: str) -> str:
    """End an evaluation's run and return the state it ended in."""
    return request("POST", f"/api/runs/{run_id}/end", {"state": state})["state"]
