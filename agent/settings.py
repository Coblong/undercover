"""The agent's settings, read from the environment when they are needed.

A real environment variable always wins over a value in `.env`.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_BACKEND_URL = "http://localhost:8000"
DEFAULT_AGENT_URL = "http://127.0.0.1:8002"


def load_environment(path: Path | None = None) -> None:
    """Add the values in `.env` to the environment, without replacing any."""
    load_dotenv(path, override=False)


def backend_url() -> str:
    """Return the address of the UnderCover backend."""
    return os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL)


def agent_url() -> str:
    """Return the address of the agent's service."""
    return os.getenv("UNDERCOVER_AGENT") or DEFAULT_AGENT_URL


def model_name() -> str | None:
    """Return the model asked for in MODEL_NAME, if any."""
    return os.getenv("MODEL_NAME") or None


def langfuse_configured() -> bool:
    """Say whether the Langfuse keys are set, so claims are traced."""
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def api_key() -> str:
    """Return the OpenRouter key, or say clearly that it is missing."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise AgentError(
            "OPENROUTER_API_KEY is not set. Put it in .env or the environment"
        )
    return key


class AgentError(Exception):
    """A failure whose message is safe to show the team."""
