"""UnderCover's payout rules for an approved claim.

The rules come from section 10 of the policy wording, the plan limits in
data/plans.json and sections 6, 7 and 10 of the claims manual.
"""

from functools import cache
from math import ceil, inf
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.db import load, payout_limit

CASH_LIMIT = 100.0
HOTEL_NIGHT_LIMIT = 350.0
MEALS_PER_TRAVELER_PER_DAY = 35.0

Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]

BENEFIT_MAXIMUM = {
    "trip_cancellation": "trip_cancellation_max",
    "trip_interruption": "trip_cancellation_max",
    "trip_delay": "trip_delay_max",
    "baggage_delay": "baggage_delay_max",
    "baggage": "baggage_max",
    "passport": "passport_max",
    "emergency_medical": "emergency_medical_max",
}


class Line(BaseModel):
    """One covered item or expense on a claim."""

    description: str = Field(description="What the item or expense is")
    amount: float = Field(
        allow_inf_nan=False,
        ge=0,
        description="The total claimed for the line in US dollars, before any limits."
        " For several items, the total for all of them",
    )
    quantity: int = Field(
        ge=1,
        description="How many separate items the line holds, such as 3 for three"
        " shirts. A pair or a set is one item. Send 1 for anything else",
    )
    kind: Literal[
        "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
    ] = Field(
        description=(
            "item: a belonging, or a pair or set that counts as one item. "
            "electronics: only a phone, tablet, laptop, camera or its lens,"
            " e-reader or smartwatch; any other device is an item. "
            "cash: cash that was lost or stolen. "
            "expense: any other cost with a receipt, such as a medical bill, a meal,"
            " a taxi, a fee or a prepaid trip cost. "
            "hotel: a hotel during a trip delay. "
            "meals_without_receipts: meals during a trip delay with no receipts."
        )
    )
    nights: int = Field(
        ge=0, description="For a hotel, the number of nights. Send 0 for anything else"
    )
    hours: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="For meals without receipts, the length of the delay in hours."
        " Send 0 for anything else",
    )


class PayoutRequest(BaseModel):
    benefit: Benefit = Field(description="The benefit the claim is paid under")
    lines: list[Line] = Field(
        max_length=100,
        description="The covered items and expenses, one for each line of the claim,"
        " with any excluded ones left out",
    )
    paid_by_others: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Anything a carrier, hotel or other party has paid or refunded"
        " for the same loss. Send 0 if nothing",
    )


class PayoutStep(BaseModel):
    step: str
    amount: float


class Payout(BaseModel):
    calculation: list[PayoutStep]
    payout: float
    needs_sign_off: bool


@cache
def plans() -> dict[str, Any]:
    """Return the plan limits from data/plans.json."""
    return load("plans.json")


def plan_of(policy: dict[str, Any]) -> dict[str, Any]:
    """Return the limits of the plan a policy is on."""
    return next(p for p in plans()["plans"] if p["plan"] == policy["plan"])


def step(description: str, amount: float) -> PayoutStep:
    """Make one line of the calculation, rounded to the cent."""
    return PayoutStep(step=description, amount=round(amount, 2))


def limit_for(line: Line, policy: dict[str, Any]) -> float:
    """Return the most UnderCover pays for one line, or infinity if it has none."""
    plan = plan_of(policy)
    travelers = len(policy["travelers"])
    match line.kind:
        case "item":
            return plan["per_item_limit"] * line.quantity
        case "electronics":
            per_item = plan["per_item_limit"]
            if "electronics_plus" in policy["add_ons"]:
                add_on = next(
                    a for a in plans()["add_ons"] if a["add_on"] == "electronics_plus"
                )
                per_item = add_on["electronics_per_item_limit"]
            return per_item * line.quantity
        case "hotel":
            return HOTEL_NIGHT_LIMIT * ceil(travelers / 2) * line.nights
        case "meals_without_receipts":
            days = ceil(line.hours / 24)
            return MEALS_PER_TRAVELER_PER_DAY * travelers * days
        case _:
            return inf


def eligible_amount(line: Line, policy: dict[str, Any]) -> PayoutStep:
    """Return the line's step: the amount claimed, or its limit if that is lower."""
    limit = limit_for(line, policy)
    if line.amount > limit:
        return step(f"{line.description}, limited to ${limit:,.2f}", limit)
    return step(line.description, line.amount)


def benefit_maximum(policy: dict[str, Any], benefit: Benefit) -> float:
    """Return the most the plan pays for a benefit.

    Trip Cancellation and Interruption are also capped at the trip cost.
    """
    maximum = plan_of(policy)[BENEFIT_MAXIMUM[benefit]]
    if benefit in ("trip_cancellation", "trip_interruption"):
        return min(maximum, policy["trip"]["trip_cost"])
    return maximum


def work_out_payout(policy: dict[str, Any], body: PayoutRequest) -> Payout:
    """Work out the payout on an approved claim, following section 10 of the wording."""
    calculation = [
        eligible_amount(line, policy) for line in body.lines if line.kind != "cash"
    ]
    cash = sum(line.amount for line in body.lines if line.kind == "cash")
    if cash:
        calculation.append(
            step(f"Cash, up to ${CASH_LIMIT:,.0f} in all", min(cash, CASH_LIMIT))
        )
    total = sum(entry.amount for entry in calculation)

    if body.paid_by_others:
        calculation.append(
            step("Less what others have paid or refunded", -body.paid_by_others)
        )
        total -= body.paid_by_others

    if body.benefit in plans()["deductible_applies_to"] and total > 0:
        deductible = min(plan_of(policy)["deductible"], total)
        if deductible:
            calculation.append(step("Less the deductible", -deductible))
            total -= deductible

    maximum = benefit_maximum(policy, body.benefit)
    if total > maximum:
        calculation.append(
            step(
                f"Less the amount over the maximum of ${maximum:,.0f}", maximum - total
            )
        )
        total = maximum

    if total < 0:
        calculation.append(step("Nothing is left to pay", -total))
        total = 0.0

    payout = round(total, 2)
    return Payout(
        calculation=calculation, payout=payout, needs_sign_off=payout > payout_limit()
    )
