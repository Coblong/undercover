from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import instructions


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
    history = client.get("/api/claims/CLM-1001/claim-history").json()
    assert history["customer_id"] == "CUS-3001"
    assert len(history["previous_claims"]) == 1
    text = str(history)
    for private in ("email", "phone", "date_of_birth", "address", "Wayne Street"):
        assert private not in text


def test_sections_of_each_document(client: TestClient):
    wording = client.get("/api/documents/wording/sections").json()
    manual = client.get("/api/documents/manual/sections").json()
    assert len(wording) == 12
    assert wording[9] == {"number": 10, "title": "How we work out your payment"}
    assert len(manual) == 14
    assert manual[8] == {"number": 9, "title": "Repeat claims"}


def test_one_section(client: TestClient):
    section = client.get("/api/documents/manual/sections/9").json()
    assert section["document"] == "manual"
    assert section["title"] == "Repeat claims"
    assert section["text"].startswith("## 9. Repeat claims")
    assert "third claim within any 12 month period" in section["text"]
    assert "## 10." not in section["text"]
    last = client.get("/api/documents/wording/sections/12").json()
    assert last["text"].startswith("## 12. Definitions")


def test_unknown_sections_and_documents(client: TestClient):
    assert client.get("/api/documents/wording/sections/13").status_code == 404
    assert client.get("/api/documents/handbook/sections").status_code == 422


def test_instructions_for_both_agents(client: TestClient):
    claim = client.get("/api/instructions/claim").json()["text"]
    queue = client.get("/api/instructions/queue").json()["text"]
    assert "get_document_section" in claim
    assert "work_claim" in queue
    assert client.get("/api/instructions/other").status_code == 422


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
        "/api/claims/CLM-9999/claim-history",
    ):
        assert client.get(path).status_code == 404


def test_the_instructions_carry_the_numbers_from_company_data(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    text = client.get("/api/instructions/claim").json()["text"]
    assert "approval of $1,000 or less" in text
    assert "within 90 days" in text
    assert "filed 3 claims within 12 months" in text

    changed = {
        "payout_approval_limit": 750,
        "claim_filing_window_days": 60,
        "repeat_claim_review": {"claims": 4, "within_months": 6},
    }
    monkeypatch.setattr(instructions, "load", lambda name: changed)
    text = client.get("/api/instructions/claim").json()["text"]
    assert "approval of $750 or less" in text
    assert "within 60 days" in text
    assert "filed 4 claims within 6 months" in text


def test_the_instruction_files_hold_no_hard_coded_numbers():
    for name in ("claim", "queue"):
        raw = (
            Path(instructions.__file__).parent / f"instructions_{name}.md"
        ).read_text()
        for number in ("$1,000", "90 days", "12 months"):
            assert number not in raw
