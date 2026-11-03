"""Steps and attempts: what the agent did on a claim, and how its work ended."""

from typing import Literal

from fastapi import HTTPException
from sqlalchemy import Connection, func, insert, select, update

from backend import queries, runs
from backend import tables as t
from backend.db import now, payout_limit, transaction
from backend.decisions import settled_state
from backend.models import Outcome, State, Step, StepRequest

MAX_FAILED_ATTEMPTS = 2

Unfinished = Literal["not_decided", "not_paid"]

UNFINISHED_TEXT: dict[Unfinished, str] = {
    "not_decided": "The agent finished without deciding the claim.",
    "not_paid": "The agent approved the claim but did not pay it.",
}


def record_step(conn: Connection, claim_id: str, body: StepRequest) -> Step:
    """Insert a step. The caller holds the transaction."""
    queries.claim_state(conn, claim_id)
    created_at = now()
    step_id = conn.execute(
        insert(t.steps)
        .values(
            claim_id=claim_id, kind=body.kind, text=body.text, created_at=created_at
        )
        .returning(t.steps.c.id)
    ).scalar_one()
    return Step(
        id=step_id,
        kind=body.kind,
        text=body.text,
        created_at=created_at,
    )


def add_step(conn: Connection, claim_id: str, body: StepRequest) -> Step:
    """Record one thing the agent did on a claim."""
    with transaction(conn):
        return record_step(conn, claim_id, body)


def start_attempt(
    conn: Connection, claim_id: str, run_id: int | None = None
) -> Outcome:
    """Mark a waiting claim as in progress and record the attempt, or refuse.

    In a run, a claim the run has not started yet counts against its limit.
    """
    with transaction(conn):
        state = queries.claim_state(conn, claim_id, lock=True)
        if run_id is not None:
            runs.check_can_start(conn, run_id, claim_id)
        if state != "waiting":
            raise HTTPException(
                409, f"Claim {claim_id} is {state}, so the agent cannot start on it"
            )
        conn.execute(
            update(t.claims)
            .where(t.claims.c.claim_id == claim_id)
            .values(state="in_progress")
        )
        conn.execute(
            insert(t.attempts).values(
                claim_id=claim_id, run_id=run_id, started_at=now()
            )
        )
    return Outcome(
        claim_id=claim_id, state="in_progress", message="The agent has started."
    )


def unfinished(conn: Connection, claim_id: str) -> Unfinished | None:
    """Say what is missing from a claim's work, or None when it is complete."""
    decision = conn.execute(
        select(t.decisions.c.decision, t.decisions.c.payout).where(
            t.decisions.c.claim_id == claim_id
        )
    ).first()
    if decision is None:
        return "not_decided"
    paid = conn.execute(
        select(t.payments.c.claim_id).where(t.payments.c.claim_id == claim_id)
    ).first()
    if (
        decision.decision == "approve"
        and decision.payout <= payout_limit()
        and not paid
    ):
        return "not_paid"
    return None


def end_attempt(conn: Connection, claim_id: str, error: str | None) -> State:
    """End an attempt and return the state the claim rests in.

    A claim with unfinished work goes back to waiting, or is left for a handler
    once two attempts have failed. Finished work settles as the decision says.
    Everything happens in one transaction, so a failure part way changes nothing.
    """
    with transaction(conn):
        queries.claim_state(conn, claim_id, lock=True)
        reason = unfinished(conn, claim_id)
        problem = error or (UNFINISHED_TEXT[reason] if reason else None)
        if problem is not None:
            record_step(conn, claim_id, StepRequest(kind="error", text=problem))
        conn.execute(
            update(t.attempts)
            .where(t.attempts.c.failed.is_(None), t.attempts.c.claim_id == claim_id)
            .values(failed=problem is not None)
        )
        new_state: State
        if reason is None:
            decision = conn.execute(
                select(t.decisions.c.decision, t.decisions.c.payout).where(
                    t.decisions.c.claim_id == claim_id
                )
            ).one()
            new_state = settled_state(decision.decision, decision.payout)
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
            update(t.claims)
            .where(t.claims.c.claim_id == claim_id)
            .values(state=new_state)
        )
    return new_state


def failed_attempts(conn: Connection, claim_id: str) -> int:
    """Count the attempts on a claim that failed."""
    return conn.execute(
        select(func.count())
        .select_from(t.attempts)
        .where(t.attempts.c.claim_id == claim_id, t.attempts.c.failed.is_(True))
    ).scalar_one()


def recover(conn: Connection) -> None:
    """Settle claims left in progress by a backend that stopped."""
    stuck = conn.execute(
        select(t.claims.c.claim_id).where(t.claims.c.state == "in_progress")
    ).all()
    for row in stuck:
        end_attempt(conn, row.claim_id, "The backend stopped during this attempt.")
