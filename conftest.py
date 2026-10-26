from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import main
from backend import work
from backend.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client for the backend on a fresh, seeded database."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "test.db"))
    with TestClient(app) as client:
        yield client


class InProcessClient(TestClient):
    """A client that can be used in a `with` block without restarting the app."""

    def __enter__(self) -> "InProcessClient":
        return self

    def __exit__(self, *args: object) -> None:
        pass


@pytest.fixture
def in_process_backend(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the agent reach the backend in this process, without a server."""
    monkeypatch.setattr(main, "backend", lambda timeout=30: InProcessClient(app))


class Agents:
    """Replaces the agents the backend runs with ones that need no model."""

    def claim(self, runner: work.ClaimAgent) -> None:
        app.dependency_overrides[work.claim_agent] = lambda: runner

    def queue(self, runner: work.QueueAgent) -> None:
        app.dependency_overrides[work.queue_agent] = lambda: runner


@pytest.fixture
def agents() -> Iterator[Agents]:
    """Let a test choose what the claim agent and the queue agent do."""
    yield Agents()
    app.dependency_overrides.clear()
