"""Decisions, payments and sign-off, with the rules that protect payouts."""

import sqlite3

from fastapi import HTTPException

from backend import queries
from backend.db import now, payout_limit
from backend.models import (
    DecisionRequest,
    Outcome,
    PaymentRequest,
    SignOffRequest,
    Step,
    StepRequest,
)


def money(amount: float) -> str:
    """Format an amount in US dollars."""
    return f"${amount:,.2f}"


def decide(conn: sqlite3.Connection, claim_id: str, body: DecisionRequest) -> Outcome:
    """Record the decision on a claim that is waiting or in progress."""
    state = queries.claim_state(conn, claim_id)
    if state not in ("waiting", "in_progress"):
        raise HTTPException(
            409, f"Claim {claim_id} is already {state}, so it cannot be decided"
        )
    payout = round(body.payout, 2)
    if body.decision == "approve" and payout <= 0:
        raise HTTPException(422, "An approval needs a payout above zero")
    if body.decision != "approve" and payout != 0:
        raise HTTPException(422, "A decline or a referral has a payout of zero")
    needs_sign_off = body.decision == "approve" and payout > payout_limit()
    new_state = "waiting_for_sign_off" if needs_sign_off else "decided"
    with conn:
        conn.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
            (
                claim_id,
                body.decision,
                payout,
                body.explanation,
                body.policy_section,
                now(),
            ),
        )
        conn.execute(
            "UPDATE claims SET state = ? WHERE claim_id = ?", (new_state, claim_id)
        )
    message = f"Decision recorded: {body.decision}."
    if needs_sign_off:
        message += (
            f" The payout is over {money(payout_limit())}, so payment waits"
            " until a handler signs off."
        )
    return Outcome(claim_id=claim_id, state=new_state, message=message)


def pay(conn: sqlite3.Connection, claim_id: str, body: PaymentRequest) -> Outcome:
    """Pay an approved claim, unless the payout is over the limit and not signed off."""
    claim = queries.get_claim(conn, claim_id)
    if claim.decision is None or claim.decision.decision != "approve":
        raise HTTPException(
            409, f"Claim {claim_id} has no approval, so there is nothing to pay"
        )
    if claim.payment is not None:
        raise HTTPException(409, f"Claim {claim_id} has already been paid")
    amount = round(body.amount, 2)
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
    with conn:
        conn.execute("INSERT INTO payments VALUES (?, ?, ?)", (claim_id, amount, now()))
    return Outcome(
        claim_id=claim_id, state=claim.state, message=f"Paid {money(amount)}."
    )


def sign_off(conn: sqlite3.Connection, claim_id: str, body: SignOffRequest) -> Outcome:
    """Record a handler's sign-off on a large payout and make the payment."""
    state = queries.claim_state(conn, claim_id)
    if state != "waiting_for_sign_off":
        raise HTTPException(
            409, f"Claim {claim_id} is {state}, not waiting for sign-off"
        )
    handler = conn.execute(
        "SELECT name, can_approve_over_limit FROM handlers WHERE handler_id = ?",
        (body.handler_id,),
    ).fetchone()
    if handler is None:
        raise queries.not_found("handler", body.handler_id)
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            403, f"{handler['name']} cannot sign off payouts over the limit"
        )
    payout = conn.execute(
        "SELECT payout FROM decisions WHERE claim_id = ?", (claim_id,)
    ).fetchone()["payout"]
    with conn:
        conn.execute(
            "INSERT INTO sign_offs VALUES (?, ?, ?)", (claim_id, body.handler_id, now())
        )
        conn.execute("INSERT INTO payments VALUES (?, ?, ?)", (claim_id, payout, now()))
        conn.execute(
            "UPDATE claims SET state = 'decided' WHERE claim_id = ?", (claim_id,)
        )
    return Outcome(
        claim_id=claim_id,
        state="decided",
        message=f"{handler['name']} signed off. Paid {money(payout)}.",
    )


def add_step(conn: sqlite3.Connection, claim_id: str, body: StepRequest) -> Step:
    """Record one thing the agent did on a claim."""
    queries.claim_state(conn, claim_id)
    created_at = now()
    with conn:
        cursor = conn.execute(
            "INSERT INTO steps (claim_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
            (claim_id, body.kind, body.text, created_at),
        )
    return Step(
        id=cursor.lastrowid or 0, kind=body.kind, text=body.text, created_at=created_at
    )


def start_run(conn: sqlite3.Connection, claim_id: str) -> Outcome:
    """Mark a waiting claim as in progress, or refuse if it is not waiting."""
    state = queries.claim_state(conn, claim_id)
    with conn:
        started = conn.execute(
            "UPDATE claims SET state = 'in_progress'"
            " WHERE claim_id = ? AND state = 'waiting'",
            (claim_id,),
        ).rowcount
    if not started:
        raise HTTPException(
            409, f"Claim {claim_id} is {state}, so the agent cannot start on it"
        )
    return Outcome(
        claim_id=claim_id, state="in_progress", message="The agent has started."
    )


def finish_run(conn: sqlite3.Connection, claim_id: str, error: str | None) -> None:
    """End a run. A claim the agent left undecided goes back to waiting."""
    if error is not None:
        add_step(conn, claim_id, StepRequest(kind="error", text=error))
    if queries.claim_state(conn, claim_id) == "in_progress":
        if error is None:
            add_step(
                conn,
                claim_id,
                StepRequest(
                    kind="error", text="The agent finished without deciding the claim."
                ),
            )
        with conn:
            conn.execute(
                "UPDATE claims SET state = 'waiting' WHERE claim_id = ?", (claim_id,)
            )
