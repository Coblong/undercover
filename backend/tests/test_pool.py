import asyncio
from collections.abc import Iterator

import anyio.to_thread
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event

from backend import db
from backend.main import app

REQUESTS = 12


def tiny_engine(url: str) -> Engine:
    """One connection, no overflow, a short wait: what every request fights over."""
    engine = create_engine(
        url, pool_size=1, max_overflow=0, pool_timeout=5, connect_args={"timeout": 10}
    )
    event.listen(engine, "connect", db.sqlite_connect)
    event.listen(engine, "begin", db.sqlite_begin)
    return engine


@pytest.fixture
def one_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    db.dispose_engines()
    monkeypatch.setattr(db, "make_engine", tiny_engine)
    yield
    db.dispose_engines()


def test_more_requests_than_connections_and_threads_all_get_answered(
    one_connection: None,
):
    """Waiting requests must not keep a connection's holder from finishing.

    Here 12 requests fight over one connection with only 3 threads between them.
    When a connection was taken in one thread and returned in another, the
    waiters filled the threads and every request timed out waiting for a
    connection that could not be returned.
    """

    async def hit(session: httpx.AsyncClient, path: str) -> int:
        return (await session.get(path)).status_code

    async def run() -> list[int]:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 3
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30
        ) as session:
            paths = ["/api/claims/CLM-1001", "/api/claims", "/api/handlers"]
            return await asyncio.gather(
                *(hit(session, paths[n % 3]) for n in range(REQUESTS))
            )

    assert asyncio.run(run()) == [200] * REQUESTS
