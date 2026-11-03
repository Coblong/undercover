import json
from typing import Any

from fastapi.testclient import TestClient

from backend import payout
from backend.payout import Benefit, Line, PayoutRequest


def policy(plan: str = "premium", add_ons: list[str] | None = None, travelers: int = 2):
    return {
        "plan": plan,
        "add_ons": add_ons or [],
        "travelers": ["A", "B", "C", "D"][:travelers],
        "trip": {"trip_cost": 3000.0},
    }


def line(kind: str, amount: float, **extra: Any) -> Line:
    fields: dict[str, Any] = {
        "description": kind,
        "amount": amount,
        "quantity": 1,
        "kind": kind,
        "nights": 0,
        "hours": 0,
    }
    return Line.model_validate({**fields, **extra})


def work(
    benefit: Benefit,
    lines: list[Line],
    paid_by_others: float = 0,
    **kwargs: Any,
) -> payout.Payout:
    body = PayoutRequest(benefit=benefit, lines=lines, paid_by_others=paid_by_others)
    return payout.work_out_payout(policy(**kwargs), body)


def test_a_plain_expense_is_paid_in_full():
    result = work("trip_delay", [line("expense", 120)])
    assert result.payout == 120
    assert not result.needs_sign_off


def test_an_item_is_limited_per_item_and_a_set_counts_as_one():
    assert work("baggage", [line("item", 500)]).payout == 250
    assert work("baggage", [line("item", 500, quantity=2)]).payout == 500 - 50


def test_electronics_have_a_higher_limit_with_the_add_on():
    plain = work("baggage", [line("electronics", 1000)])
    plus = work("baggage", [line("electronics", 1000)], add_ons=["electronics_plus"])
    assert plain.payout == 300 - 50
    assert plus.payout == 1000 - 50


def test_cash_is_capped_at_100_in_all():
    result = work("baggage", [line("cash", 80), line("cash", 80)])
    assert result.payout == 100 - 50


def test_hotel_nights_are_limited_by_rooms():
    two = work("trip_delay", [line("hotel", 2000, nights=2)], travelers=2)
    four = work("trip_delay", [line("hotel", 2000, nights=2)], travelers=4)
    assert two.payout == 700
    assert four.payout == 800


def test_meals_without_receipts_are_limited_by_travelers_and_days():
    result = work("trip_delay", [line("meals_without_receipts", 500, hours=30)])
    assert result.payout == 35 * 2 * 2


def test_what_others_paid_comes_off_first():
    result = work("trip_delay", [line("expense", 500)], paid_by_others=200)
    assert result.payout == 300
    assert result.calculation[1].step == "Less what others have paid or refunded"


def test_the_deductible_applies_only_to_some_benefits():
    assert work("baggage", [line("expense", 200)]).payout == 150
    assert work("emergency_medical", [line("expense", 200)]).payout == 150
    assert work("trip_delay", [line("expense", 200)]).payout == 200
    assert work("baggage", [line("expense", 30)]).payout == 0


def test_the_benefit_maximum_caps_the_payout():
    result = work("trip_delay", [line("expense", 5000)])
    assert result.payout == 800
    assert result.calculation[-1].step.startswith("Less the amount over the maximum")


def test_trip_cancellation_is_capped_at_the_trip_cost():
    assert work("trip_cancellation", [line("expense", 4000)]).payout == 3000
    assert work("trip_interruption", [line("expense", 4000)]).payout == 3000


def test_nothing_left_to_pay():
    result = work("trip_delay", [line("expense", 100)], paid_by_others=150)
    assert result.payout == 0
    assert result.calculation[-1].step == "Nothing is left to pay"


def test_sign_off_is_needed_only_above_the_limit_in_company_json():
    assert not work("emergency_medical", [line("expense", 1050)]).needs_sign_off
    assert work("emergency_medical", [line("expense", 1050.01)]).needs_sign_off
    assert work("emergency_medical", [line("expense", 1050.01)]).payout == 1000.01


def body(**changes: Any) -> dict[str, Any]:
    line_ = {
        "description": "Medical bill",
        "amount": 420,
        "quantity": 1,
        "kind": "expense",
        "nights": 0,
        "hours": 0,
    }
    return {
        "benefit": "trip_delay",
        "lines": [line_],
        "paid_by_others": 0,
    } | changes


def test_the_operation_works_out_the_payout_for_a_claim(client: TestClient):
    response = client.post("/api/claims/CLM-1001/payout", json=body())
    assert response.status_code == 200
    assert response.json()["payout"] == 420
    assert response.json()["needs_sign_off"] is False


def test_the_operation_refuses_what_makes_no_sense(client: TestClient):
    missing = client.post("/api/claims/CLM-9999/payout", json=body())
    assert missing.status_code == 404
    for bad in (
        body(benefit="theft"),
        body(paid_by_others=-1),
        body(lines=[{"description": "x"}]),
        {"benefit": "baggage", "lines": []},
    ):
        assert client.post("/api/claims/CLM-1001/payout", json=bad).status_code == 422


def test_an_amount_that_is_not_finite_is_refused(client: TestClient):
    raw = json.dumps(body()).replace('"amount": 420', '"amount": NaN')
    response = client.post(
        "/api/claims/CLM-1001/payout",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
