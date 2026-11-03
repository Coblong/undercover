from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from agent import service
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
    service._working.clear()  # noqa: SLF001


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
    service._working.add("CLM-1001")  # noqa: SLF001
    assert work(agent_service).status_code == 409


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
    assert service._working == set()  # noqa: SLF001


def test_it_will_not_start_without_a_database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "")
    service.app.state.checkpointer = None
    with (
        pytest.raises(AgentError, match="DATABASE_URL is not set"),
        TestClient(service.app),
    ):
        pass
