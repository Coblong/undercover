from sqlalchemy import Connection

from backend import runs
from backend.models import CreateDispatchRequest, Dispatch


def new_dispatch(conn: Connection, concurrency: int = 3) -> Dispatch:
    """Start a dispatch on the model the product uses by default."""
    return runs.create_dispatch(
        conn,
        CreateDispatchRequest(
            concurrency=concurrency,
            model="openai/gpt-6-luna",
            reasoning_effort="low",
        ),
    )
