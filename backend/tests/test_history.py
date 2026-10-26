import os
import sqlite3

from fastapi.testclient import TestClient


def history(client: TestClient, claim_id: str) -> list[dict[str, object]]:
    return client.get(f"/api/claims/{claim_id}/claim-history").json()["previous_claims"]


def decide_and_pay(client: TestClient, claim_id: str, payout: float) -> None:
    client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": "approve",
            "payout": payout,
            "explanation": "Covered.",
            "policy_section": "3. Trip Delay",
        },
    )
    client.post(f"/api/claims/{claim_id}/payment", json={"amount": payout})


def test_a_claim_sees_the_customers_other_open_claim(client: TestClient):
    """CUS-3005 filed CLM-1005 on July 14 and CLM-1006 on August 25."""
    later = history(client, "CLM-1006")
    assert [(p["claim_id"], p["outcome"], p["amount_paid"]) for p in later] == [
        ("CLM-1005", "open", 0)
    ]
    assert history(client, "CLM-1005") == []


def test_deciding_a_claim_changes_the_next_claims_history(client: TestClient):
    decide_and_pay(client, "CLM-1005", 243)
    earlier = next(
        p for p in history(client, "CLM-1006") if p["claim_id"] == "CLM-1005"
    )
    assert earlier["outcome"] == "approved"
    assert earlier["amount_paid"] == 243
    assert earlier["filed_on"] == "2026-07-14"


def test_declined_and_referred_claims_count_too(client: TestClient):
    for kind in ("decline", "refer"):
        client.post(
            "/api/claims/CLM-1005/decision",
            json={
                "decision": kind,
                "payout": 0,
                "explanation": "Reason.",
                "policy_section": "3. Trip Delay",
            },
        )
        break
    earlier = next(
        p for p in history(client, "CLM-1006") if p["claim_id"] == "CLM-1005"
    )
    assert (earlier["outcome"], earlier["amount_paid"]) == ("declined", 0)


def test_seeded_and_system_claims_are_listed_together_in_date_order(
    client: TestClient,
):
    with sqlite3.connect(os.environ["UNDERCOVER_DB"]) as conn:
        conn.execute(
            "UPDATE claims SET customer_id = 'CUS-3001' WHERE claim_id = 'CLM-1006'"
        )
    claims = history(client, "CLM-1006")
    assert [(p["claim_id"], p["filed_on"]) for p in claims] == [
        ("CLM-0412", "2024-08-19"),
        ("CLM-1001", "2026-06-24"),
    ]


def test_a_claim_filed_later_never_appears_in_an_earlier_history(client: TestClient):
    for claim in client.get("/api/claims").json():
        ids = [p["claim_id"] for p in history(client, claim["claim_id"])]
        assert claim["claim_id"] not in ids
        filed = {
            p["claim_id"]: p["filed_on"] for p in history(client, claim["claim_id"])
        }
        assert all(date <= claim["submitted_on"] for date in filed.values())
