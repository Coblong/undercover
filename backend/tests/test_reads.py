from fastapi.testclient import TestClient


def test_list_claims(client: TestClient):
    claims = client.get("/api/claims").json()
    assert len(claims) == 25
    assert {c["state"] for c in claims} == {"waiting"}
    assert claims[0]["claim_id"] == "CLM-1001"
    assert claims[0]["customer_name"] == "Maya Patel"
    assert claims[0]["amount_claimed"] == 420.0


def test_get_claim(client: TestClient):
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["policy_id"] == "POL-2001"
    assert "Rome" in claim["story"]
    assert sum(item["amount"] for item in claim["items"]) == 420.0
    assert len(claim["documents"]) == 4
    assert claim["decision"] is None
    assert claim["payment"] is None
    assert claim["sign_off"] is None


def test_get_policy(client: TestClient):
    policy = client.get("/api/policies/POL-2001").json()
    assert policy["plan"] == "premium"
    assert policy["trip"]["destination"] == "Rome, Italy"


def test_claim_history_has_no_contact_details(client: TestClient):
    history = client.get("/api/customers/CUS-3001/claim-history").json()
    assert history["customer_id"] == "CUS-3001"
    assert len(history["previous_claims"]) == 1
    text = str(history)
    for private in ("email", "phone", "date_of_birth", "address", "Wayne Street"):
        assert private not in text


def test_policy_wording(client: TestClient):
    text = client.get("/api/policy-wording").json()["text"]
    assert text.startswith("# UnderCover Travel Insurance: Policy Wording")
    assert "## 10. How we work out your payment" in text


def test_instructions(client: TestClient):
    assert client.get("/api/instructions").json()["text"]


def test_list_handlers(client: TestClient):
    handlers = client.get("/api/handlers").json()
    assert [h["name"] for h in handlers] == [
        "Dana Whitfield",
        "Marcus Oyelaran",
        "Priya Raman",
    ]
    assert "email" not in handlers[0]


def test_steps_start_empty(client: TestClient):
    assert client.get("/api/claims/CLM-1001/steps").json() == []


def test_unknown_ids_are_404(client: TestClient):
    for path in (
        "/api/claims/CLM-9999",
        "/api/claims/CLM-9999/steps",
        "/api/policies/POL-9999",
        "/api/customers/CUS-9999/claim-history",
    ):
        assert client.get(path).status_code == 404
