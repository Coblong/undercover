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
    State,
    Step,
    StepRequest,
)

MAX_FAILED_ATTEMPTS = 2


def money(amount: float) -> str:
    """Format an amount in US dollars."""
    return f"${amount:,.2f}"


def settled_state(decision: str, payout: float) -> State:
    """Return the state a claim rests in once it has this decision."""
    if decision == "approve" and payout > payout_limit():
        return "waiting_for_sign_off"
    return "decided"


def decide(conn: sqlite3.Connection, claim_id: str, body: DecisionRequest) -> Outcome:
    """Record the decision on a claim that is waiting or in progress.

    A claim the agent is working on stays in progress until its run ends.
    """
    state = queries.claim_state(conn, claim_id)
    if state not in ("waiting", "in_progress"):
        raise HTTPException(
            409, f"Claim {claim_id} is already {state}, so it cannot be decided"
        )
    if conn.execute(
        "SELECT 1 FROM decisions WHERE claim_id = ?", (claim_id,)
    ).fetchone():
        raise HTTPException(409, f"Claim {claim_id} already has a decision")
    payout = round(body.payout, 2)
    if body.decision == "approve" and payout <= 0:
        raise HTTPException(422, "An approval needs a payout above zero")
    if body.decision != "approve" and payout != 0:
        raise HTTPException(422, "A decline or a referral has a payout of zero")
    settled = settled_state(body.decision, payout)
    new_state: State = "in_progress" if state == "in_progress" else settled
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
    if settled == "waiting_for_sign_off":
        message += (
            f" The payout is over {money(payout_limit())}, so payment waits"
            " until a handler signs off."
        )
    elif body.decision == "approve":
        message += f" Now pay {money(payout)} with pay_claim."
    return Outcome(claim_id=claim_id, state=new_state, message=message)


def pay(conn: sqlite3.Connection, claim_id: str, body: PaymentRequest) -> Outcome:
    """Pay an approved claim, unless the payout is over the limit and not signed off."""
    claim = queries.get_claim(conn, claim_id)
    if claim.state == "left_for_handler":
        raise HTTPException(
            409, f"Claim {claim_id} is left for a handler, so it cannot be paid here"
        )
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


def start_attempt(
    conn: sqlite3.Connection, claim_id: str, run_id: int | None = None
) -> Outcome:
    """Mark a waiting claim as in progress and record the attempt, or refuse."""
    state = queries.claim_state(conn, claim_id)
    with conn:
        started = conn.execute(
            "UPDATE claims SET state = 'in_progress'"
            " WHERE claim_id = ? AND state = 'waiting'",
            (claim_id,),
        ).rowcount
        if started:
            conn.execute(
                "INSERT INTO attempts (claim_id, run_id, started_at) VALUES (?, ?, ?)",
                (claim_id, run_id, now()),
            )
    if not started:
        raise HTTPException(
            409, f"Claim {claim_id} is {state}, so the agent cannot start on it"
        )
    return Outcome(
        claim_id=claim_id, state="in_progress", message="The agent has started."
    )


def unfinished_reason(conn: sqlite3.Connection, claim_id: str) -> str | None:
    """Say what is missing from a claim's work, or None when it is complete."""
    decision = conn.execute(
        "SELECT decision, payout FROM decisions WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if decision is None:
        return "The agent finished without deciding the claim."
    paid = conn.execute(
        "SELECT 1 FROM payments WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if (
        decision["decision"] == "approve"
        and decision["payout"] <= payout_limit()
        and not paid
    ):
        return "The agent approved the claim but did not pay it."
    return None


def end_attempt(conn: sqlite3.Connection, claim_id: str, error: str | None) -> State:
    """End an attempt and return the state the claim rests in.

    A claim with unfinished work goes back to waiting, or is left for a handler
    once two attempts have failed. Finished work settles as the decision says.
    """
    if error is not None:
        add_step(conn, claim_id, StepRequest(kind="error", text=error))
    reason = unfinished_reason(conn, claim_id)
    if reason is not None and error is None:
        add_step(conn, claim_id, StepRequest(kind="error", text=reason))
    failed = error is not None or reason is not None
    with conn:
        conn.execute(
            "UPDATE attempts SET failed = ? WHERE failed IS NULL AND claim_id = ?",
            (int(failed), claim_id),
        )
    new_state: State
    if reason is None:
        decision = conn.execute(
            "SELECT decision, payout FROM decisions WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        new_state = settled_state(decision["decision"], decision["payout"])
    elif failed_attempts(conn, claim_id) >= MAX_FAILED_ATTEMPTS:
        add_step(
            conn,
            claim_id,
            StepRequest(
                kind="error",
                text="The claim failed twice, so it is left for a handler.",
            ),
        )
        new_state = "left_for_handler"
    else:
        new_state = "waiting"
    with conn:
        conn.execute(
            "UPDATE claims SET state = ? WHERE claim_id = ?", (new_state, claim_id)
        )
    return new_state


def failed_attempts(conn: sqlite3.Connection, claim_id: str) -> int:
    """Count the attempts on a claim that failed."""
    return conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE claim_id = ? AND failed = 1", (claim_id,)
    ).fetchone()[0]


def recover(conn: sqlite3.Connection) -> None:
    """Settle claims left in progress by a backend that stopped."""
    stuck = conn.execute("SELECT claim_id FROM claims WHERE state = 'in_progress'")
    for row in stuck.fetchall():
        end_attempt(conn, row["claim_id"], "The backend stopped during this attempt.")
