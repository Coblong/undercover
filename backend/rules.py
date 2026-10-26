"""Decisions, payments and sign-off, with the rules that protect payouts."""

import sqlite3

from fastapi import HTTPException

from backend import documents, queries, runs
from backend.db import now, payout_limit, transaction
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


def decide(conn: sqlite3.Connection, claim_id: str, body: DecisionRequest) -> Outcome:
    """Record the decision on a claim that is waiting or in progress.

    A claim the agent is working on stays in progress until its run ends.
    """
    payout = round(body.payout, 2)
    with transaction(conn):
        state = queries.claim_state(conn, claim_id)
        if state not in ("waiting", "in_progress"):
            raise HTTPException(
                409, f"Claim {claim_id} is already {state}, so it cannot be decided"
            )
        if conn.execute(
            "SELECT 1 FROM decisions WHERE claim_id = ?", (claim_id,)
        ).fetchone():
            raise HTTPException(409, f"Claim {claim_id} already has a decision")
        claimed = conn.execute(
            "SELECT amount_claimed FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()["amount_claimed"]
        check_decision(body, payout, claimed)
        settled = settled_state(body.decision, payout)
        new_state: State = "in_progress" if state == "in_progress" else settled
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
    amount = round(body.amount, 2)
    with transaction(conn):
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
            raise HTTPException(409, f"Claim {claim_id} has already been paid")
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
        conn.execute("INSERT INTO payments VALUES (?, ?, ?)", (claim_id, amount, now()))
    return Outcome(
        claim_id=claim_id, state=claim.state, message=f"Paid {money(amount)}."
    )


def sign_off(conn: sqlite3.Connection, claim_id: str, body: SignOffRequest) -> Outcome:
    """Record a handler's sign-off on a large payout and make the payment."""
    with transaction(conn):
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


def record_step(conn: sqlite3.Connection, claim_id: str, body: StepRequest) -> Step:
    """Insert a step. The caller holds the transaction."""
    queries.claim_state(conn, claim_id)
    created_at = now()
    cursor = conn.execute(
        "INSERT INTO steps (claim_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
        (claim_id, body.kind, body.text, created_at),
    )
    return Step(
        id=cursor.lastrowid or 0, kind=body.kind, text=body.text, created_at=created_at
    )


def add_step(conn: sqlite3.Connection, claim_id: str, body: StepRequest) -> Step:
    """Record one thing the agent did on a claim."""
    with transaction(conn):
        return record_step(conn, claim_id, body)


def start_attempt(
    conn: sqlite3.Connection, claim_id: str, run_id: int | None = None
) -> Outcome:
    """Mark a waiting claim as in progress and record the attempt, or refuse.

    In a run, a claim the run has not started yet counts against its limit.
    """
    with transaction(conn):
        state = queries.claim_state(conn, claim_id)
        if run_id is not None:
            runs.check_can_start(conn, run_id, claim_id)
        if state != "waiting":
            raise HTTPException(
                409, f"Claim {claim_id} is {state}, so the agent cannot start on it"
            )
        conn.execute(
            "UPDATE claims SET state = 'in_progress' WHERE claim_id = ?", (claim_id,)
        )
        conn.execute(
            "INSERT INTO attempts (claim_id, run_id, started_at) VALUES (?, ?, ?)",
            (claim_id, run_id, now()),
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
    Everything happens in one transaction, so a failure part way changes nothing.
    """
    with transaction(conn):
        reason = unfinished_reason(conn, claim_id)
        problem = error or reason
        if problem is not None:
            record_step(conn, claim_id, StepRequest(kind="error", text=problem))
        conn.execute(
            "UPDATE attempts SET failed = ? WHERE failed IS NULL AND claim_id = ?",
            (int(problem is not None), claim_id),
        )
        new_state: State
        if reason is None:
            decision = conn.execute(
                "SELECT decision, payout FROM decisions WHERE claim_id = ?",
                (claim_id,),
            ).fetchone()
            new_state = settled_state(decision["decision"], decision["payout"])
        elif failed_attempts(conn, claim_id) >= MAX_FAILED_ATTEMPTS:
            record_step(
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


def result_line(conn: sqlite3.Connection, claim_id: str) -> str:
    """Say in one line how the work on a claim ended."""
    claim = queries.get_claim(conn, claim_id)
    decision = claim.decision
    if claim.state == "left_for_handler":
        return f"{claim_id} failed twice and is left for a handler."
    if claim.state == "waiting" or decision is None:
        problem = conn.execute(
            "SELECT text FROM steps WHERE claim_id = ? AND kind = 'error'"
            " ORDER BY id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        return (
            f"{claim_id} failed ({problem['text']}) and is waiting to be tried again."
        )
    line = f"{claim_id} decided: {decision.decision}"
    if decision.decision != "approve":
        return line + "."
    if claim.payment is not None:
        return f"{line} {money(decision.payout)}, paid."
    return f"{line} {money(decision.payout)}, waiting for a handler's sign-off."
