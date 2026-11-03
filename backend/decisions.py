"""Decisions, payments and sign-off, with the rules that protect payouts."""

from typing import cast

from fastapi import HTTPException
from sqlalchemy import Connection, insert, select, update

from backend import attempts, documents, queries
from backend import tables as t
from backend.db import now, payout_limit, transaction
from backend.models import (
    DecisionRequest,
    Outcome,
    PaymentRequest,
    SignOffRequest,
    State,
)


def money(amount: float) -> str:
    """Format an amount in US dollars."""
    return f"${amount:,.2f}"


def check_decision(body: DecisionRequest, payout: float, claimed: float) -> None:
    """Refuse a decision whose payout or reasons make no sense."""
    if body.decision == "approve" and payout <= 0:
        raise HTTPException(422, "An approval needs a payout above zero")
    if body.decision != "approve" and payout != 0:
        raise HTTPException(422, "A decline or a referral has a payout of zero")
    if payout > claimed:
        raise HTTPException(
            422, f"The payout is more than the {money(claimed)} claimed"
        )
    if not documents.is_section(body.policy_section):
        raise HTTPException(
            422,
            f'"{body.policy_section}" is not a section of the policy wording or'
            ' the claims manual. Give a number and a title, such as "3. Trip Delay"',
        )


def decide(conn: Connection, claim_id: str, body: DecisionRequest) -> Outcome:
    """Record the decision on a claim that is waiting or in progress.

    A claim the agent is working on stays in progress until its attempt ends.
    The same decision again counts as done; a different one is refused.
    """
    payout = round(body.payout, 2)
    with transaction(conn):
        state = queries.claim_state(conn, claim_id, lock=True)
        recorded = conn.execute(
            select(t.decisions.c.decision, t.decisions.c.payout).where(
                t.decisions.c.claim_id == claim_id
            )
        ).first()
        if recorded is not None:
            if (recorded.decision, recorded.payout) != (body.decision, payout):
                raise HTTPException(409, f"Claim {claim_id} already has a decision")
            return Outcome(
                claim_id=claim_id,
                state=cast(State, state),
                message=f"Decision already recorded: {body.decision}.",
            )
        if state not in ("waiting", "in_progress"):
            raise HTTPException(
                409, f"Claim {claim_id} is already {state}, so it cannot be decided"
            )
        claimed = conn.execute(
            select(t.claims.c.amount_claimed).where(t.claims.c.claim_id == claim_id)
        ).scalar_one()
        check_decision(body, payout, claimed)
        settled = attempts.settled_state(body.decision, payout)
        new_state: State = "in_progress" if state == "in_progress" else settled
        conn.execute(
            insert(t.decisions).values(
                claim_id=claim_id,
                decision=body.decision,
                payout=payout,
                explanation=body.explanation,
                policy_section=body.policy_section,
                decided_at=now(),
            )
        )
        conn.execute(
            update(t.claims)
            .where(t.claims.c.claim_id == claim_id)
            .values(state=new_state)
        )
    message = f"Decision recorded: {body.decision}."
    if settled == "waiting_for_sign_off":
        message += (
            f" The payout is over {money(payout_limit())}, so payment waits"
            " until a handler signs off."
        )
    elif body.decision == "approve":
        message += f" {money(payout)} will be paid."
    return Outcome(claim_id=claim_id, state=new_state, message=message)


def pay(conn: Connection, claim_id: str, body: PaymentRequest) -> Outcome:
    """Pay an approved claim, unless the payout is over the limit and not signed off.

    The same payment again counts as done; a different one is refused.
    """
    amount = round(body.amount, 2)
    with transaction(conn):
        queries.claim_state(conn, claim_id, lock=True)
        claim = queries.get_claim(conn, claim_id)
        if claim.state == "left_for_handler":
            raise HTTPException(
                409,
                f"Claim {claim_id} is left for a handler, so it cannot be paid here",
            )
        if claim.decision is None or claim.decision.decision != "approve":
            raise HTTPException(
                409, f"Claim {claim_id} has no approval, so there is nothing to pay"
            )
        if claim.payment is not None:
            if amount != claim.payment.amount:
                raise HTTPException(409, f"Claim {claim_id} has already been paid")
            return Outcome(
                claim_id=claim_id,
                state=claim.state,
                message=f"Already paid {money(amount)}.",
            )
        if amount != claim.decision.payout:
            raise HTTPException(
                409,
                f"The approved payout is {money(claim.decision.payout)}."
                " Pay exactly that amount",
            )
        if amount > payout_limit() and claim.sign_off is None:
            raise HTTPException(
                409,
                f"Payment blocked: {money(amount)} is over the {money(payout_limit())}"
                " limit and a handler has not signed off",
            )
        conn.execute(
            insert(t.payments).values(
                claim_id=claim_id,
                amount=amount,
                paid_at=now(),
                signed_off_by=claim.sign_off.handler_id if claim.sign_off else None,
                signed_off_at=claim.sign_off.signed_off_at if claim.sign_off else None,
            )
        )
    return Outcome(
        claim_id=claim_id, state=claim.state, message=f"Paid {money(amount)}."
    )


def sign_off(conn: Connection, claim_id: str, body: SignOffRequest) -> Outcome:
    """Record a handler's sign-off on a large payout and hand the claim to the agent.

    The claim is in progress until the agent has paid it.
    """
    with transaction(conn):
        state = queries.claim_state(conn, claim_id, lock=True)
        if state != "waiting_for_sign_off":
            raise HTTPException(
                409, f"Claim {claim_id} is {state}, not waiting for sign-off"
            )
        handler = conn.execute(
            select(t.handlers.c.name, t.handlers.c.can_approve_over_limit).where(
                t.handlers.c.handler_id == body.handler_id
            )
        ).first()
        if handler is None:
            raise queries.not_found("handler", body.handler_id)
        if not handler.can_approve_over_limit:
            raise HTTPException(
                403, f"{handler.name} cannot sign off payouts over the limit"
            )
        conn.execute(
            insert(t.sign_offs).values(
                claim_id=claim_id, handler_id=body.handler_id, signed_off_at=now()
            )
        )
        conn.execute(
            update(t.claims)
            .where(t.claims.c.claim_id == claim_id)
            .values(state="in_progress")
        )
        attempts.open_resume_attempt(conn, claim_id)
    return Outcome(
        claim_id=claim_id,
        state="in_progress",
        message=f"{handler.name} signed off. The agent will pay it.",
    )
