from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import improver_client, main
from backend import agent_service, db
from backend.main import app
from backend.tests import signing


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep every test off Supabase, Langfuse and the real agent service.

    The values are set, not removed, so that loading `.env` does not replace them.
    Port 9 refuses connections, so a test that reaches for the service fails at once.
    """
    for name in ("DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://127.0.0.1:9")
    yield
    db.dispose_engines()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client for the backend on a fresh, seeded database, signed in as Dana."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "test.db"))
    signing.install(monkeypatch)
    with TestClient(app) as client:
        signing.sign_in_as(client, signing.DANA)
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
    monkeypatch.setattr(improver_client, "backend", lambda: InProcessClient(app))


class Agents:
    """Replaces the agents the backend asks for with ones that need no model."""

    def claim(self, runner: agent_service.ClaimAgent) -> None:
        app.dependency_overrides[agent_service.claim_agent] = lambda: runner

    def evaluation(self, starter: agent_service.EvaluationStarter) -> None:
        app.dependency_overrides[agent_service.evaluation_starter] = lambda: starter

    def dispatch(self, starter: agent_service.DispatchStarter) -> None:
        app.dependency_overrides[agent_service.dispatch_starter] = lambda: starter

    def round_start(self, starter: agent_service.RoundStarter) -> None:
        app.dependency_overrides[agent_service.round_starter] = lambda: starter

    def round_resume(self, resumer: agent_service.Resumer) -> None:
        app.dependency_overrides[agent_service.round_resumer] = lambda: resumer


@pytest.fixture
def agents() -> Iterator[Agents]:
    """Let a test choose what the claim agent does."""
    yield Agents()
    app.dependency_overrides.clear()
