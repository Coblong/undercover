import httpx
import pytest

from agent.settings import AgentError
from backend import work


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
    assert work.call_service("CLM-1001", "CLM-1001-ab") == "approve 420.00"
    assert seen["url"] == "http://agent.test:8002/claims/CLM-1001/work"
    assert seen["json"] == {"thread_id": "CLM-1001-ab"}


def test_a_service_that_is_not_running_is_unreachable(monkeypatch: pytest.MonkeyPatch):
    answer(monkeypatch, httpx.ConnectError("refused"))
    with pytest.raises(work.AgentUnreachable):
        work.call_service("CLM-1001", "t")


def test_a_service_that_goes_away_midway_has_stopped(monkeypatch: pytest.MonkeyPatch):
    answer(monkeypatch, httpx.ReadError("reset"))
    with pytest.raises(work.AgentStopped):
        work.call_service("CLM-1001", "t")


def test_a_service_that_fails_is_an_error(monkeypatch: pytest.MonkeyPatch):
    answer(monkeypatch, httpx.Response(500, text="boom"))
    with pytest.raises(AgentError, match="boom"):
        work.call_service("CLM-1001", "t")
