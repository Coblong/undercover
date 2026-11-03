"""Dispatches: the claims a run works, how it ends, and what it cost."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import Connection, func, insert, select, update

from backend import queries, usage
from backend import tables as t
from backend.db import now, transaction
from backend.models import (
    CreateDispatchRequest,
    Dispatch,
    DispatchClaim,
    DispatchSummary,
    HeldPayout,
    RunClaims,
    StoppedRuns,
)

CLAIMS_TO_WORK = ("waiting", "in_progress")


def seconds_between(start: str, end: str | None) -> float:
    """Return the seconds from a time to another, or to now when there is no end."""
    stop = datetime.fromisoformat(end) if end else datetime.now(UTC)
    return (stop - datetime.fromisoformat(start)).total_seconds()


def get_run(conn: Connection, run_id: int, lock: bool = False):
    """Return a run's row, or raise a 404. With `lock`, hold its row."""
    query = select(t.runs).where(t.runs.c.id == run_id)
    row = conn.execute(query.with_for_update() if lock else query).mappings().first()
    if row is None:
        raise queries.not_found("run", str(run_id))
    return row


def running_run(conn: Connection, run_id: int, lock: bool = False):
    """Return a run that is still going, or raise a 409."""
    run = get_run(conn, run_id, lock)
    if run["state"] != "running":
        raise HTTPException(409, f"Run {run_id} has already {run['state']}")
    return run


def create_dispatch(conn: Connection, body: CreateDispatchRequest) -> Dispatch:
    """Start a dispatch, unless a dispatch or an evaluation is already going."""
    with transaction(conn):
        if conn.execute(select(t.runs.c.id).where(t.runs.c.state == "running")).first():
            raise HTTPException(409, "A dispatch or an evaluation is already running")
        run_id = conn.execute(
            insert(t.runs)
            .values(
                kind="dispatch",
                concurrency=body.concurrency,
                model=body.model,
                reasoning_effort=body.reasoning_effort,
                state="running",
                started_at=now(),
            )
            .returning(t.runs.c.id)
        ).scalar_one()
    return get_dispatch(conn, run_id)


def plan(conn: Connection, run_id: int) -> RunClaims:
    """Return the claims a run works, choosing and recording them for a dispatch.

    A dispatch works every claim in progress and every waiting claim, oldest
    submitted first. Asking again returns the same claims.
    """
    with transaction(conn):
        running_run(conn, run_id, lock=True)
        chosen = claims_of(conn, run_id)
        if not chosen:
            rows = conn.execute(
                select(t.claims.c.claim_id)
                .where(t.claims.c.state.in_(CLAIMS_TO_WORK))
                .order_by(t.claims.c.submitted_on, t.claims.c.claim_id)
            )
            chosen = [row.claim_id for row in rows]
            if chosen:
                conn.execute(
                    insert(t.run_claims),
                    [
                        {"run_id": run_id, "claim_id": claim_id, "position": i}
                        for i, claim_id in enumerate(chosen)
                    ],
                )
    return RunClaims(run_id=run_id, claim_ids=chosen)


def claims_of(conn: Connection, run_id: int) -> list[str]:
    """Return a run's claims in order."""
    rows = conn.execute(
        select(t.run_claims.c.claim_id)
        .where(t.run_claims.c.run_id == run_id)
        .order_by(t.run_claims.c.position)
    )
    return [row.claim_id for row in rows]


def end(conn: Connection, run_id: int, state: str) -> Dispatch:
    """End a run that is going."""
    with transaction(conn):
        running_run(conn, run_id, lock=True)
        conn.execute(
            update(t.runs)
            .where(t.runs.c.id == run_id)
            .values(state=state, ended_at=now())
        )
    return get_dispatch(conn, run_id)


def stop_all(conn: Connection) -> StoppedRuns:
    """Mark every run still going as stopped, as the service does when it starts."""
    with transaction(conn):
        result = conn.execute(
            update(t.runs)
            .where(t.runs.c.state == "running")
            .values(state="stopped", ended_at=now())
        )
    return StoppedRuns(stopped=result.rowcount)


def summarize(claims: list[DispatchClaim]) -> DispatchSummary:
    """Count a dispatch's decisions and list what needs a handler, from its claims."""
    return DispatchSummary(
        approved=sum(c.decision == "approve" for c in claims),
        declined=sum(c.decision == "decline" for c in claims),
        referred=sum(c.decision == "refer" for c in claims),
        waiting_for_sign_off=[
            HeldPayout(claim_id=c.claim_id, payout=c.payout or 0)
            for c in claims
            if c.state == "waiting_for_sign_off"
        ],
        left_for_handler=[c.claim_id for c in claims if c.state == "left_for_handler"],
        in_progress=[c.claim_id for c in claims if c.state == "in_progress"],
    )


def get_dispatch(conn: Connection, run_id: int) -> Dispatch:
    """Return a dispatch with its claims, its cost and time, and its summary."""
    run = get_run(conn, run_id)
    costs = usage.costs_by_claim(conn, run_id)
    times = usage.seconds_by_claim(conn, run_id)
    rows = conn.execute(
        select(
            t.run_claims.c.claim_id,
            t.run_claims.c.started_at,
            t.claims.c.state,
            t.decisions.c.decision,
            t.decisions.c.payout,
        )
        .select_from(t.run_claims)
        .join(t.claims, t.claims.c.claim_id == t.run_claims.c.claim_id)
        .outerjoin(t.decisions, t.decisions.c.claim_id == t.run_claims.c.claim_id)
        .where(t.run_claims.c.run_id == run_id)
        .order_by(t.run_claims.c.position)
    ).mappings()
    claims = [
        DispatchClaim(
            **row,
            seconds=times.get(row["claim_id"], 0.0),
            cost=costs.get(row["claim_id"], 0.0),
        )
        for row in rows
    ]
    return Dispatch(
        run_id=run_id,
        concurrency=run["concurrency"],
        model=run["model"],
        reasoning_effort=run["reasoning_effort"],
        state=run["state"],
        started_at=run["started_at"],
        ended_at=run["ended_at"],
        seconds=seconds_between(run["started_at"], run["ended_at"]),
        claim_seconds=usage.run_seconds(conn, run_id),
        cost=usage.run_cost(conn, run_id),
        claims=claims,
        summary=None if run["state"] == "running" else summarize(claims),
    )


def get_latest_dispatch(conn: Connection) -> Dispatch | None:
    """Return the most recent dispatch, or None before the first."""
    latest = conn.execute(
        select(func.max(t.runs.c.id)).where(t.runs.c.kind == "dispatch")
    ).scalar_one()
    return None if latest is None else get_dispatch(conn, latest)
