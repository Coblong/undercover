"""Steps and attempts: what the agent did on a claim, and how its work ended."""

from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, delete, func, insert, select, update

from backend import queries, runs
from backend import tables as t
from backend.db import now, payout_limit, transaction
from backend.models import Outcome, StartedClaim, State, Step, StepRequest

MAX_FAILED_ATTEMPTS = 2

Unfinished = Literal["not_decided", "not_paid"]

UNFINISHED_TEXT: dict[Unfinished, str] = {
    "not_decided": "The agent finished without deciding the claim.",
    "not_paid": "The agent approved the claim but did not pay it.",
}


def settled_state(decision: str, payout: float, copy: bool = False) -> State:
    """Return the state a claim rests in once it has this decision.

    An evaluation's copy is never held for a sign-off: nobody can sign it off, so
    a held approval counts as finished.
    """
    if decision == "approve" and payout > payout_limit() and not copy:
        return "waiting_for_sign_off"
    return "decided"


def new_thread(claim_id: str) -> str:
    """Name a thread for a claim: the claim id and a few random characters."""
    return f"{claim_id}-{uuid4().hex[:8]}"


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


def start_attempt(conn: Connection, claim_id: str) -> Outcome:
    """Mark a waiting claim as in progress and record the attempt, or refuse."""
    with transaction(conn):
        begin_attempt(conn, claim_id, queries.claim_state(conn, claim_id, lock=True))
    return Outcome(
        claim_id=claim_id, state="in_progress", message="The agent has started."
    )


def begin_attempt(
    conn: Connection, claim_id: str, state: str, run_id: int | None = None
) -> str:
    """Start an attempt on a waiting claim and return its thread. In a transaction."""
    if state != "waiting":
        raise HTTPException(
            409, f"Claim {claim_id} is {state}, so the agent cannot start on it"
        )
    thread_id = new_thread(claim_id)
    conn.execute(
        update(t.claims)
        .where(t.claims.c.claim_id == claim_id)
        .values(state="in_progress")
    )
    conn.execute(
        insert(t.attempts).values(
            claim_id=claim_id, run_id=run_id, started_at=now(), thread_id=thread_id
        )
    )
    return thread_id


def start_claim(conn: Connection, run_id: int, claim_id: str) -> StartedClaim:
    """Start a waiting claim in a run, or carry on the claim in progress.

    A waiting claim gets a new attempt on a new thread. A claim in progress
    carries on its open attempt on the same thread, as the same attempt.
    """
    with transaction(conn):
        runs.running_run(conn, run_id, lock=True)
        state = queries.claim_state(conn, claim_id, lock=True)
        member = conn.execute(
            update(t.run_claims)
            .where(
                t.run_claims.c.run_id == run_id,
                t.run_claims.c.claim_id == claim_id,
                t.run_claims.c.started_at.is_(None),
            )
            .values(started_at=now())
        )
        if not member.rowcount and claim_id not in runs.claims_of(conn, run_id):
            raise HTTPException(409, f"Claim {claim_id} is not in run {run_id}")
        evaluation = queries.claim_evaluation(conn, claim_id) is not None
        resumed = state == "in_progress"
        if resumed:
            thread_id = open_thread(conn, claim_id)
            if thread_id is None:
                raise HTTPException(
                    409, f"Claim {claim_id} is in progress with no work to carry on"
                )
        else:
            thread_id = begin_attempt(conn, claim_id, state, run_id)
    return StartedClaim(
        claim_id=claim_id, thread_id=thread_id, resumed=resumed, evaluation=evaluation
    )


def open_thread(conn: Connection, claim_id: str) -> str | None:
    """Return the thread of a claim's open attempt, or None if it has none."""
    return conn.execute(
        select(t.attempts.c.thread_id).where(
            t.attempts.c.claim_id == claim_id, t.attempts.c.failed.is_(None)
        )
    ).scalar_one_or_none()


def open_resume_attempt(conn: Connection, claim_id: str) -> None:
    """Open an attempt on a claim's last thread. The caller holds the transaction."""
    last = conn.execute(
        select(t.attempts.c.thread_id)
        .where(t.attempts.c.claim_id == claim_id)
        .order_by(t.attempts.c.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    conn.execute(
        insert(t.attempts).values(
            claim_id=claim_id,
            started_at=now(),
            thread_id=last or new_thread(claim_id),
        )
    )


def carry_on(conn: Connection, claim_id: str) -> Outcome:
    """Check that a claim in progress has an open attempt to carry on, or refuse."""
    with transaction(conn):
        state = queries.claim_state(conn, claim_id, lock=True)
        if state != "in_progress" or open_thread(conn, claim_id) is None:
            raise HTTPException(
                409, f"Claim {claim_id} is {state}, so there is no work to carry on"
            )
    return Outcome(
        claim_id=claim_id, state="in_progress", message="The agent carries on."
    )


def release(conn: Connection, claim_id: str, problem: str) -> None:
    """Take back an attempt that never reached the agent, without counting it."""
    with transaction(conn):
        queries.claim_state(conn, claim_id, lock=True)
        conn.execute(
            delete(t.attempts).where(
                t.attempts.c.claim_id == claim_id, t.attempts.c.failed.is_(None)
            )
        )
        record_step(conn, claim_id, StepRequest(kind="error", text=problem))
        conn.execute(
            update(t.claims)
            .where(t.claims.c.claim_id == claim_id)
            .values(state="waiting")
        )


def unfinished(conn: Connection, claim_id: str) -> Unfinished | None:
    """Say what is missing from a claim's work, or None when it is complete."""
    claim = queries.get_claim(conn, claim_id)
    if claim.decision is None:
        return "not_decided"
    payable = claim.decision.payout <= payout_limit() or claim.sign_off is not None
    if claim.decision.decision == "approve" and payable and claim.payment is None:
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
            held = queries.get_claim(conn, claim_id)
            copy = held.evaluation_id is not None
            new_state = (
                "waiting_for_sign_off"
                if held.needs_sign_off and not copy
                else "decided"
            )
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
