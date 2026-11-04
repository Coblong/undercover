"""What the routes are given: a connection and the agents."""

import functools
import inspect
from collections.abc import Callable
from contextlib import closing
from typing import Annotated, Any

from fastapi import Depends

from backend import agent_service
from backend.db import connect

ClaimAgent = Annotated[agent_service.ClaimAgent, Depends(agent_service.claim_agent)]
DispatchStarter = Annotated[
    agent_service.DispatchStarter, Depends(agent_service.dispatch_starter)
]
EvaluationStarter = Annotated[
    agent_service.EvaluationStarter, Depends(agent_service.evaluation_starter)
]


def with_db[**P, R](route: Callable[P, R]) -> Callable[..., R]:
    """Give a route its `conn` in the route's own thread, and take it back there.

    A connection from a dependency would be taken in one thread, used in another
    and returned in a third. With more requests waiting for a connection than the
    pool holds, the waiters fill the threads, and those holding a connection can
    never get a thread to finish and return it. Here a thread that waits holds no
    connection, and a thread that holds one always has what it needs to finish.
    """
    signature = inspect.signature(route)
    kept = [p for name, p in signature.parameters.items() if name != "conn"]

    @functools.wraps(route)
    def wrapper(*args: Any, **kwargs: Any) -> R:
        with closing(connect()) as conn:
            return route(*args, conn=conn, **kwargs)  # pyright: ignore[reportCallIssue]

    wrapper.__signature__ = signature.replace(parameters=kept)  # type: ignore[attr-defined]
    return wrapper
