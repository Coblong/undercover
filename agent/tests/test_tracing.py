from typing import Any

import pytest

from agent import settings, tracing


class FakeHandler:
    pass


def test_without_keys_a_run_is_not_traced(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert tracing.config_for("CLM-1001-ab12") == {
        "configurable": {"thread_id": "CLM-1001-ab12"}
    }
    assert not settings.langfuse_configured()
    tracing.flush()


def test_with_keys_the_thread_is_the_langfuse_session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(tracing, "CallbackHandler", FakeHandler)
    config = tracing.config_for("CLM-1003-1a2b3c4d")
    assert config["configurable"] == {"thread_id": "CLM-1003-1a2b3c4d"}
    assert config["metadata"] == {"langfuse_session_id": "CLM-1003-1a2b3c4d"}
    assert isinstance(config["callbacks"][0], FakeHandler)


def test_a_run_is_flushed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    flushed: list[Any] = []

    class Client:
        def flush(self) -> None:
            flushed.append(True)

    monkeypatch.setattr(tracing, "get_client", Client)
    tracing.flush()
    assert flushed == [True]
