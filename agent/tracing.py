"""Tracing in Langfuse, with one session for each claim's thread."""

from typing import Any

from langfuse import get_client
from langfuse.langchain import CallbackHandler

from agent import settings


def config_for(thread_id: str) -> dict[str, Any]:
    """Return the run config for a thread, traced in Langfuse when its keys are set.

    The session is named for the thread, which starts with the claim id, so a
    claim's work carried on after a pause lands in the same session.
    """
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if settings.langfuse_configured():
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {"langfuse_session_id": thread_id}
    return config


def flush() -> None:
    """Send the traces now, since a short run or a stopped service would lose them."""
    if settings.langfuse_configured():
        get_client().flush()
