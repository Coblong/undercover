import httpx
import pytest
from fastapi import HTTPException

from agent.settings import AgentError
from backend import agent_service


def answer(monkeypatch: pytest.MonkeyPatch, result: httpx.Response | Exception):
    seen: dict[str, object] = {}

    def post(url: str, **kwargs: object) -> httpx.Response:
        seen.update(url=url, **kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", post)
    return seen


def test_the_service_is_asked_to_work_a_claim_on_a_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test:8002")
    seen = answer(monkeypatch, httpx.Response(200, json={"reply": "approve 420.00"}))
    assert agent_service.call_service("CLM-1001", "CLM-1001-ab") == "approve 420.00"
    assert seen["url"] == "http://agent.test:8002/claims/CLM-1001/work"
    assert seen["json"] == {"thread_id": "CLM-1001-ab"}


def test_a_service_that_is_not_running_is_unreachable(monkeypatch: pytest.MonkeyPatch):
    answer(monkeypatch, httpx.ConnectError("refused"))
    with pytest.raises(agent_service.AgentUnreachable):
        agent_service.call_service("CLM-1001", "t")


def test_a_service_that_goes_away_midway_has_stopped(monkeypatch: pytest.MonkeyPatch):
    answer(monkeypatch, httpx.ReadError("reset"))
    with pytest.raises(agent_service.AgentStopped):
        agent_service.call_service("CLM-1001", "t")


def test_a_service_that_fails_is_an_error(monkeypatch: pytest.MonkeyPatch):
    answer(monkeypatch, httpx.Response(500, text="boom"))
    with pytest.raises(AgentError, match="boom"):
        agent_service.call_service("CLM-1001", "t")


def test_a_claim_the_service_is_already_working_is_a_stop_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    answer(monkeypatch, httpx.Response(409, json={"detail": "already being worked"}))
    with pytest.raises(agent_service.AgentStopped):
        agent_service.call_service("CLM-1001", "t")


def test_a_dispatch_is_started_by_asking_the_service(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test:8002")
    seen = answer(monkeypatch, httpx.Response(202, json={"run_id": 4}))
    assert agent_service.start_dispatch(7) == 4
    assert seen["url"] == "http://agent.test:8002/dispatches"
    assert seen["json"] == {"concurrency": 7}


def test_a_service_that_is_not_running_cannot_start_a_dispatch(
    monkeypatch: pytest.MonkeyPatch,
):
    answer(monkeypatch, httpx.ConnectError("refused"))
    with pytest.raises(HTTPException) as refused:
        agent_service.start_dispatch(3)
    assert refused.value.status_code == 503
    assert refused.value.detail == "The agent's service could not be reached."


def test_the_services_refusal_comes_back_in_its_own_words(
    monkeypatch: pytest.MonkeyPatch,
):
    answer(monkeypatch, httpx.Response(409, json={"detail": "A dispatch is running"}))
    with pytest.raises(HTTPException) as refused:
        agent_service.start_dispatch(3)
    assert (refused.value.status_code, refused.value.detail) == (
        409,
        "A dispatch is running",
    )
    answer(monkeypatch, httpx.Response(500, text="boom"))
    with pytest.raises(HTTPException) as broken:
        agent_service.start_dispatch(3)
    assert broken.value.status_code == 502
