import pytest
from fastapi.testclient import TestClient


def decision(**changes: object) -> dict[str, object]:
    return {
        "decision": "approve",
        "payout": 100,
        "explanation": "Covered.",
        "policy_section": "3. Trip Delay",
        **changes,
    }


def post(client: TestClient, body: str | dict[str, object], claim_id="CLM-1001"):
    path = f"/api/claims/{claim_id}/decision"
    if isinstance(body, str):
        return client.post(
            path, content=body, headers={"content-type": "application/json"}
        )
    return client.post(path, json=body)


@pytest.mark.parametrize(
    "changes",
    [
        {"payout": 420.01},
        {"payout": 10_000},
        {"payout": -5},
        {"payout": 0},
        {"explanation": ""},
        {"explanation": "   "},
        {"explanation": "x" * 2001},
        {"policy_section": ""},
        {"policy_section": "Section 3"},
        {"policy_section": "7. Dental"},
        {"policy_section": "3. Trip delay"},
    ],
)
def test_a_senseless_decision_is_refused_and_changes_nothing(
    client: TestClient, changes: dict[str, object]
):
    assert post(client, decision(**changes)).status_code == 422
    claim = client.get("/api/claims/CLM-1001").json()
    assert (claim["state"], claim["decision"]) == ("waiting", None)


@pytest.mark.parametrize("payout", ["NaN", "Infinity", "-Infinity"])
def test_a_payout_that_is_not_a_number_is_refused(client: TestClient, payout: str):
    body = (
        f'{{"decision": "approve", "payout": {payout}, "explanation": "x",'
        ' "policy_section": "3. Trip Delay"}'
    )
    assert post(client, body).status_code == 422
    assert client.get("/api/claims/CLM-1001").json()["decision"] is None


def test_a_payment_that_is_not_a_number_is_refused(client: TestClient):
    response = client.post(
        "/api/claims/CLM-1001/payment",
        content='{"amount": NaN}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


def test_the_refusal_says_what_to_fix(client: TestClient):
    detail = post(client, decision(policy_section="7. Dental")).json()["detail"]
    assert '"7. Dental" is not a section' in detail
    assert 'such as "3. Trip Delay"' in detail
    assert (
        "more than the $420.00 claimed"
        in post(client, decision(payout=500)).json()["detail"]
    )


def test_sections_of_the_manual_are_accepted(client: TestClient):
    response = post(client, decision(policy_section="9. Repeat claims"))
    assert response.status_code == 200


def test_a_payout_up_to_the_amount_claimed_is_accepted(client: TestClient):
    assert post(client, decision(payout=420)).status_code == 200


def test_text_is_trimmed(client: TestClient):
    post(client, decision(explanation="  Covered.  "))
    assert client.get("/api/claims/CLM-1001").json()["decision"]["explanation"] == (
        "Covered."
    )


def test_long_texts_and_lists_are_refused(client: TestClient):
    step = {"kind": "message", "text": "x" * 20_001}
    assert client.post("/api/claims/CLM-1001/steps", json=step).status_code == 422
    step["text"] = "x" * 20_000
    assert client.post("/api/claims/CLM-1001/steps", json=step).status_code == 200
    todos = [{"text": f"CLM-{n}", "status": "to_do"} for n in range(101)]
    assert client.put("/api/runs/1/todos", json={"todos": todos}).status_code == 422
    long_item = [{"text": "x" * 301, "status": "to_do"}]
    assert client.put("/api/runs/1/todos", json={"todos": long_item}).status_code == 422
    summary = {"summary": "x" * 4001}
    assert client.post("/api/runs/1/finish", json=summary).status_code == 422
