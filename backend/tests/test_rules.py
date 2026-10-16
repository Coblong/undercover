import os
import sqlite3

from fastapi.testclient import TestClient


def decide(client: TestClient, claim_id: str, decision: str, payout: float):
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": decision,
            "payout": payout,
            "explanation": "Covered under the wording.",
            "policy_section": "3. Trip Delay",
        },
    )


def pay(client: TestClient, claim_id: str, amount: float):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def sign_off(client: TestClient, claim_id: str, handler_id: str = "HND-01"):
    return client.post(
        f"/api/claims/{claim_id}/sign-off", json={"handler_id": handler_id}
    )


def state(client: TestClient, claim_id: str) -> str:
    return client.get(f"/api/claims/{claim_id}").json()["state"]


def test_small_approval_is_decided_then_payable(client: TestClient):
    response = decide(client, "CLM-1001", "approve", 420)
    assert response.status_code == 200
    assert response.json()["state"] == "decided"
    assert response.json()["message"].endswith("Now pay $420.00 with pay_claim.")
    paid = pay(client, "CLM-1001", 420)
    assert paid.status_code == 200
    assert paid.json()["message"] == "Paid $420.00."
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["decision"]["decision"] == "approve"
    assert claim["decision"]["policy_section"] == "3. Trip Delay"
    assert claim["payment"]["amount"] == 420.0


def test_large_approval_waits_for_sign_off(client: TestClient):
    response = decide(client, "CLM-1001", "approve", 1250)
    assert response.json()["state"] == "waiting_for_sign_off"
    assert "signs off" in response.json()["message"]
    assert state(client, "CLM-1001") == "waiting_for_sign_off"


def test_payout_exactly_at_the_limit_needs_no_sign_off(client: TestClient):
    assert decide(client, "CLM-1001", "approve", 1000).json()["state"] == "decided"
    assert pay(client, "CLM-1001", 1000).status_code == 200


def test_payment_over_the_limit_is_blocked_until_sign_off(client: TestClient):
    decide(client, "CLM-1001", "approve", 1250)
    blocked = pay(client, "CLM-1001", 1250)
    assert blocked.status_code == 409
    assert "Payment blocked" in blocked.json()["detail"]
    assert client.get("/api/claims/CLM-1001").json()["payment"] is None

    signed = sign_off(client, "CLM-1001")
    assert signed.status_code == 200
    assert signed.json()["message"] == "Dana Whitfield signed off. Paid $1,250.00."
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["state"] == "decided"
    assert claim["payment"]["amount"] == 1250.0
    assert claim["sign_off"]["handler_name"] == "Dana Whitfield"
    assert pay(client, "CLM-1001", 1250).status_code == 409


def test_payment_must_match_the_approved_payout(client: TestClient):
    decide(client, "CLM-1001", "approve", 420)
    wrong = pay(client, "CLM-1001", 500)
    assert wrong.status_code == 409
    assert "$420.00" in wrong.json()["detail"]
    assert client.get("/api/claims/CLM-1001").json()["payment"] is None


def test_paying_twice_is_refused(client: TestClient):
    decide(client, "CLM-1001", "approve", 420)
    assert pay(client, "CLM-1001", 420).status_code == 200
    assert pay(client, "CLM-1001", 420).status_code == 409


def test_nothing_to_pay_without_an_approval(client: TestClient):
    assert pay(client, "CLM-1001", 100).status_code == 409
    decide(client, "CLM-1001", "decline", 0)
    assert pay(client, "CLM-1001", 100).status_code == 409


def test_decline_and_refer_are_decided_with_no_payout(client: TestClient):
    assert decide(client, "CLM-1001", "decline", 0).json()["state"] == "decided"
    assert decide(client, "CLM-1002", "refer", 0).json()["state"] == "decided"


def test_payout_must_fit_the_decision(client: TestClient):
    assert decide(client, "CLM-1001", "approve", 0).status_code == 422
    assert decide(client, "CLM-1001", "decline", 50).status_code == 422
    assert state(client, "CLM-1001") == "waiting"


def test_a_claim_is_decided_once(client: TestClient):
    decide(client, "CLM-1001", "decline", 0)
    assert decide(client, "CLM-1001", "approve", 420).status_code == 409


def test_unknown_claim_is_404(client: TestClient):
    assert decide(client, "CLM-9999", "decline", 0).status_code == 404
    assert pay(client, "CLM-9999", 1).status_code == 404
    assert sign_off(client, "CLM-9999").status_code == 404
    step = {"kind": "message", "text": "x"}
    assert client.post("/api/claims/CLM-9999/steps", json=step).status_code == 404
    assert client.post("/api/claims/CLM-9999/run").status_code == 404


def test_sign_off_needs_a_claim_waiting_for_it(client: TestClient):
    assert sign_off(client, "CLM-1001").status_code == 409
    decide(client, "CLM-1001", "approve", 420)
    assert sign_off(client, "CLM-1001").status_code == 409


def test_sign_off_by_an_unknown_handler_is_404(client: TestClient):
    decide(client, "CLM-1001", "approve", 1250)
    assert sign_off(client, "CLM-1001", "HND-99").status_code == 404
    assert state(client, "CLM-1001") == "waiting_for_sign_off"


def test_steps_are_listed_in_order(client: TestClient):
    for text in ("first", "second"):
        step = {"kind": "tool_call", "text": text}
        assert client.post("/api/claims/CLM-1001/steps", json=step).status_code == 200
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert [s["text"] for s in steps] == ["first", "second"]
    assert steps[0]["id"] < steps[1]["id"]


def test_states_across_the_queue(client: TestClient):
    decide(client, "CLM-1001", "approve", 420)
    decide(client, "CLM-1002", "approve", 1250)
    states = {c["claim_id"]: c["state"] for c in client.get("/api/claims").json()}
    assert states["CLM-1001"] == "decided"
    assert states["CLM-1002"] == "waiting_for_sign_off"
    assert states["CLM-1003"] == "waiting"


def test_handler_without_authority_cannot_sign_off(client: TestClient):
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        conn.execute(
            "UPDATE handlers SET can_approve_over_limit = 0 WHERE handler_id = 'HND-03'"
        )
    decide(client, "CLM-1001", "approve", 1250)
    assert sign_off(client, "CLM-1001", "HND-03").status_code == 403
    assert state(client, "CLM-1001") == "waiting_for_sign_off"
