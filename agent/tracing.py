"""Tracing in Langfuse, with one session for each claim's thread."""

from typing import Any

from langfuse import get_client
from langfuse.langchain import CallbackHandler

from agent import settings


def config_for(
    thread_id: str, run_id: int | None = None, round_id: int | None = None
) -> dict[str, Any]:
    """Return the run config for a thread, traced in Langfuse when its keys are set.

    The session is named for the thread, which starts with the claim id, so a
    claim's work carried on after a pause lands in the same session. A claim
    worked for a run has its trace tagged `run-<id>`, and for a round's evaluation
    `round-<id>` too, so a whole run or round is found together.
    """
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if settings.langfuse_configured():
        config["callbacks"] = [CallbackHandler()]
        metadata: dict[str, Any] = {"langfuse_session_id": thread_id}
        tags = []
        if run_id is not None:
            tags.append(f"run-{run_id}")
        if round_id is not None:
            tags.append(f"round-{round_id}")
        if tags:
            metadata["langfuse_tags"] = tags
        config["metadata"] = metadata
    return config


def round_config(thread_id: str, round_id: int) -> dict[str, Any]:
    """Return the run config for the improver's work in a round.

    All of the round's improver traces go in one Langfuse session named for the round.
    """
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "run_name": f"Improver round {round_id}",
    }
    if settings.langfuse_configured():
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {"langfuse_session_id": f"round-{round_id}"}
    return config


def flush() -> None:
    """Send the traces now, since a short run or a stopped service would lose them."""
    if settings.langfuse_configured():
        get_client().flush()
