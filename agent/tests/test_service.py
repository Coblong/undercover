import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from agent import dispatcher, main, service
from agent.settings import AgentError
from agent.tests.stubs import calls, decision, stub

pytestmark = pytest.mark.usefixtures("in_process_backend")


@pytest.fixture
def agent_service() -> Iterator[TestClient]:
    service.app.state.checkpointer = InMemorySaver()
    with TestClient(service.app, raise_server_exceptions=False) as client:
        yield client
    service.app.state.checkpointer = None
    service.app.dependency_overrides.clear()
    if service._gate.locked():  # noqa: SLF001
        service._gate.release()  # noqa: SLF001


def use_model(*replies):
    service.app.dependency_overrides[service.language_model] = lambda: stub(*replies)


def work(agent_service: TestClient, claim_id: str = "CLM-1001", thread: str = "t1"):
    return agent_service.post(f"/claims/{claim_id}/work", json={"thread_id": thread})


def test_the_service_says_it_is_running(agent_service: TestClient):
    assert agent_service.get("/health").json() == {"status": "ok"}


def test_it_starts_a_claim_on_a_thread(client: TestClient, agent_service: TestClient):
    use_model(calls(decision("approve", 420)))
    response = work(agent_service)
    assert response.status_code == 200
    assert response.json() == {"reply": "approve 420.00, paid"}
    assert client.get("/api/claims/CLM-1001").json()["payment"]["amount"] == 420.0


def test_it_ignores_a_thread_that_is_finished(
    client: TestClient, agent_service: TestClient
):
    use_model(calls(decision("approve", 420)))
    work(agent_service)
    use_model()
    again = work(agent_service)
    assert again.json() == {"reply": "approve 420.00, paid"}
    assert client.get("/api/claims/CLM-1001").json()["payment"]["amount"] == 420.0


def test_it_resumes_a_thread_once_there_is_a_sign_off(
    client: TestClient, agent_service: TestClient
):
    use_model(calls(decision("approve", 1250)))
    assert work(agent_service, "CLM-1003").json() == {"reply": "waiting for sign-off"}
    use_model()
    assert work(agent_service, "CLM-1003").json() == {"reply": "waiting for sign-off"}
    client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    assert work(agent_service, "CLM-1003").json() == {"reply": "approve 1250.00, paid"}
    claim = client.get("/api/claims/CLM-1003").json()
    assert claim["payment"]["signed_off_by"] == "HND-01"


def test_a_claim_being_worked_is_refused(agent_service: TestClient):
    with main.working("CLM-1001"):
        assert work(agent_service).status_code == 409
    use_model(calls(decision("decline", 0)))
    assert work(agent_service).status_code == 200


def test_a_failure_tells_the_backend_what_stopped(
    agent_service: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail(*args: object, **kwargs: object) -> str:
        raise AgentError("the model was called 30 times")

    monkeypatch.setattr(service.main, "run", fail)
    response = work(agent_service)
    assert response.status_code == 500
    assert response.json() == {"detail": "the model was called 30 times"}

    def crash(*args: object, **kwargs: object) -> str:
        raise KeyError("secret")

    monkeypatch.setattr(service.main, "run", crash)
    agent_service_failure = work(agent_service)
    assert agent_service_failure.json() == {"detail": "KeyError"}
    assert "secret" not in agent_service_failure.text


def test_it_will_not_start_without_a_database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "")
    service.app.state.checkpointer = None
    with (
        pytest.raises(AgentError, match="DATABASE_URL is not set"),
        TestClient(service.app),
    ):
        pass


def test_claim_agents_share_a_pool_of_four_and_wait_for_a_connection():
    pool = service.make_pool("postgresql://user:pass@localhost/none", open=False)
    assert pool.max_size == 4
    assert pool.timeout == 120


class Workers:
    """Stand-in claim workers that wait to be released and note their claims."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.worked: list[tuple[str, int]] = []

    def __call__(self, claim_id: str, run_id: int) -> str:
        self.worked.append((claim_id, run_id))
        self.release.wait(5)
        return "decided"


@pytest.fixture
def workers() -> Workers:
    stand_in = Workers()
    service.app.dependency_overrides[service.claim_worker] = lambda: stand_in
    return stand_in


def latest(client: TestClient) -> dict:
    return client.get("/api/dispatches/latest").json()


def until_ended(client: TestClient) -> dict:
    for _ in range(100):
        if latest(client)["state"] != "running":
            return latest(client)
        time.sleep(0.05)
    raise AssertionError("the dispatch never ended")


def test_a_dispatch_starts_at_once_and_is_recorded(
    client: TestClient, agent_service: TestClient, workers: Workers
):
    response = agent_service.post("/dispatches", json={"concurrency": 7})
    assert response.status_code == 202
    run = latest(client)
    assert response.json() == {"run_id": run["run_id"]}
    assert run["state"] == "running"
    assert (run["concurrency"], run["model"], run["reasoning_effort"]) == (
        7,
        "openai/gpt-6-luna",
        "low",
    )
    workers.release.set()
    ended = until_ended(client)
    assert ended["state"] == "finished"
    assert len(workers.worked) == 25
    assert {run_id for _, run_id in workers.worked} == {run["run_id"]}


def test_a_second_start_is_refused_while_one_is_running(
    client: TestClient, agent_service: TestClient, workers: Workers
):
    agent_service.post("/dispatches", json={"concurrency": 3})
    again = agent_service.post("/dispatches", json={"concurrency": 3})
    assert again.status_code == 409
    assert "already running" in again.json()["detail"]
    workers.release.set()
    first = until_ended(client)
    assert first["run_id"] == 1
    assert agent_service.post("/dispatches", json={"concurrency": 3}).status_code == 202
    until_ended(client)
    assert latest(client)["run_id"] == 2


def test_the_backend_refusing_a_start_frees_the_service(
    client: TestClient, agent_service: TestClient, workers: Workers
):
    client.post(
        "/api/runs/dispatch",
        json={"concurrency": 1, "model": "m", "reasoning_effort": "low"},
    )
    refused = agent_service.post("/dispatches", json={"concurrency": 3})
    assert refused.status_code == 409
    assert not service._gate.locked()  # noqa: SLF001


def test_a_number_of_claim_agents_out_of_range_is_refused(agent_service: TestClient):
    for concurrency in (0, 26):
        response = agent_service.post("/dispatches", json={"concurrency": concurrency})
        assert response.status_code == 422


def test_a_dispatch_whose_graph_raises_ends_failed(
    client: TestClient,
    agent_service: TestClient,
    workers: Workers,
    monkeypatch: pytest.MonkeyPatch,
):
    def explode(*args: object) -> None:
        raise RuntimeError("the graph broke")

    monkeypatch.setattr(dispatcher, "run_dispatch", explode)
    agent_service.post("/dispatches", json={"concurrency": 3})
    assert until_ended(client)["state"] == "failed"
    assert not service._gate.locked()  # noqa: SLF001


def test_starting_the_service_stops_a_run_that_was_going(client: TestClient):
    body = {"concurrency": 3, "model": "m", "reasoning_effort": "low"}
    client.post("/api/runs/dispatch", json=body)
    client.post("/api/runs/1/end", json={"state": "finished"})
    client.post("/api/runs/dispatch", json=body)
    service.app.state.checkpointer = InMemorySaver()
    try:
        with TestClient(service.app):
            assert latest(client)["state"] == "stopped"
            assert client.get("/api/dispatches/latest").json()["run_id"] == 2
    finally:
        service.app.state.checkpointer = None
    assert client.post("/api/runs/stop").json() == {"stopped": 0}


def test_the_service_waits_for_the_backend_to_come_up(
    monkeypatch: pytest.MonkeyPatch,
):
    answers = iter(["Error: the backend could not be reached"] * 2 + ["{}", "{}"])
    monkeypatch.setattr(main, "call", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)
    service.stop_stale_runs()


def test_the_service_stops_if_the_backend_never_comes_up(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(main, "call", lambda *args, **kwargs: "Error: down")
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)
    with pytest.raises(AgentError, match="backend could not be reached"):
        service.stop_stale_runs()


def test_an_evaluation_starts_with_its_set_and_is_recorded(
    client: TestClient, agent_service: TestClient, workers: Workers
):
    body = {"concurrency": 5, "eval_set": "held_out", "tiny": True}
    response = agent_service.post("/evaluations", json=body)
    assert response.status_code == 202
    detail = client.get(f"/api/evaluations/{response.json()['run_id']}").json()
    assert (detail["eval_set"], detail["tiny"], detail["concurrency"]) == (
        "held_out",
        True,
        5,
    )
    assert detail["claim_count"] == 5
    assert agent_service.post("/dispatches", json={"concurrency": 3}).status_code == 409
    workers.release.set()
    for _ in range(100):
        if (
            client.get(f"/api/evaluations/{detail['run_id']}").json()["state"]
            != "running"
        ):
            break
        time.sleep(0.05)
    ended = client.get(f"/api/evaluations/{detail['run_id']}").json()
    assert ended["state"] == "finished"
    assert len(workers.worked) == 5
    assert all(copy.startswith("EV1-CLM-11") for copy, _ in workers.worked)


def test_an_evaluation_set_that_does_not_exist_is_refused(agent_service: TestClient):
    body = {"concurrency": 5, "eval_set": "queue", "tiny": True}
    assert agent_service.post("/evaluations", json=body).status_code == 422
