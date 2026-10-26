import httpx
import pytest

from agent import main


def backend_answering(
    monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport
) -> None:
    monkeypatch.setattr(
        main,
        "backend",
        lambda timeout=30: httpx.Client(
            transport=handler, base_url="http://backend", timeout=timeout
        ),
    )


def respond(response: httpx.Response) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: response)


def test_a_good_answer_comes_back_as_text(monkeypatch: pytest.MonkeyPatch):
    backend_answering(monkeypatch, respond(httpx.Response(200, text='{"ok": true}')))
    assert main.call("GET", "/api/health") == '{"ok": true}'


def test_a_refusal_gives_its_reason(monkeypatch: pytest.MonkeyPatch):
    backend_answering(
        monkeypatch, respond(httpx.Response(409, json={"detail": "Already decided"}))
    )
    assert main.call("POST", "/x") == "Error 409: Already decided"


def test_a_500_with_a_plain_text_body_comes_back_as_text(
    monkeypatch: pytest.MonkeyPatch,
):
    backend_answering(
        monkeypatch, respond(httpx.Response(500, text="Internal Server Error"))
    )
    assert main.call("GET", "/x") == "Error 500: Internal Server Error"


def test_a_500_with_no_body_comes_back_as_text(monkeypatch: pytest.MonkeyPatch):
    backend_answering(monkeypatch, respond(httpx.Response(502)))
    assert main.call("GET", "/x") == "Error 502: Bad Gateway"


def test_a_call_that_never_reached_the_backend_is_tried_again_then_reported(
    monkeypatch: pytest.MonkeyPatch,
):
    seen: list[str] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    backend_answering(monkeypatch, httpx.MockTransport(refuse))
    assert main.call("GET", "/x") == "Error: the backend could not be reached"
    assert len(seen) == main.CONNECT_TRIES


def test_a_call_that_connects_on_a_later_try_succeeds(monkeypatch: pytest.MonkeyPatch):
    tries: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        tries.append(1)
        if len(tries) < 2:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, text="up")

    monkeypatch.setattr(main, "RETRY_DELAY", 0)
    backend_answering(monkeypatch, httpx.MockTransport(flaky))
    assert main.call("GET", "/x") == "up"


def test_a_call_that_may_have_arrived_is_not_repeated(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []

    def slow(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        raise httpx.ReadTimeout("slow", request=request)

    backend_answering(monkeypatch, httpx.MockTransport(slow))
    assert (
        main.call("POST", "/pay") == "Error: the backend did not answer (ReadTimeout)"
    )
    assert seen == ["/pay"]
