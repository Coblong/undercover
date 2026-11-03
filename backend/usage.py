"""What the agent's work costs: model calls and working time, by run."""

from typing import cast

from sqlalchemy import Connection, func, insert, select

from backend import queries
from backend import tables as t
from backend.db import now, transaction
from backend.models import ModelCallRequest, Outcome, State, WorkTimeRequest


def check_run(conn: Connection, run_id: int | None) -> None:
    """Raise a 404 if a run is named and does not exist."""
    if (
        run_id is not None
        and conn.execute(select(t.runs.c.id).where(t.runs.c.id == run_id)).first()
        is None
    ):
        raise queries.not_found("run", str(run_id))


def record_model_call(
    conn: Connection, claim_id: str, body: ModelCallRequest
) -> Outcome:
    """Record the tokens and the cost of one model call on a claim."""
    with transaction(conn):
        state = queries.claim_state(conn, claim_id)
        check_run(conn, body.run_id)
        conn.execute(
            insert(t.model_calls).values(
                claim_id=claim_id,
                run_id=body.run_id,
                input_tokens=body.input_tokens,
                output_tokens=body.output_tokens,
                cost=body.cost,
                created_at=now(),
            )
        )
    return Outcome(claim_id=claim_id, state=cast(State, state), message="Recorded.")


def record_work_time(conn: Connection, claim_id: str, body: WorkTimeRequest) -> Outcome:
    """Record the seconds the agent spent working on a claim."""
    with transaction(conn):
        state = queries.claim_state(conn, claim_id)
        check_run(conn, body.run_id)
        conn.execute(
            insert(t.work_times).values(
                claim_id=claim_id,
                run_id=body.run_id,
                seconds=body.seconds,
                created_at=now(),
            )
        )
    return Outcome(claim_id=claim_id, state=cast(State, state), message="Recorded.")


def costs_by_claim(conn: Connection, run_id: int) -> dict[str, float]:
    """Return the model cost of each claim in a run."""
    rows = conn.execute(
        select(t.model_calls.c.claim_id, func.sum(t.model_calls.c.cost).label("total"))
        .where(t.model_calls.c.run_id == run_id)
        .group_by(t.model_calls.c.claim_id)
    )
    return {row.claim_id: row.total for row in rows}


def seconds_by_claim(conn: Connection, run_id: int) -> dict[str, float]:
    """Return the seconds the agent worked on each claim in a run."""
    rows = conn.execute(
        select(t.work_times.c.claim_id, func.sum(t.work_times.c.seconds).label("total"))
        .where(t.work_times.c.run_id == run_id)
        .group_by(t.work_times.c.claim_id)
    )
    return {row.claim_id: row.total for row in rows}


def run_cost(conn: Connection, run_id: int) -> float:
    """Return what the model calls of a run cost, failed attempts included."""
    total = conn.execute(
        select(func.sum(t.model_calls.c.cost)).where(t.model_calls.c.run_id == run_id)
    ).scalar_one()
    return total or 0.0


def run_seconds(conn: Connection, run_id: int) -> float:
    """Return the seconds the agent worked on the claims of a run, all added."""
    total = conn.execute(
        select(func.sum(t.work_times.c.seconds)).where(t.work_times.c.run_id == run_id)
    ).scalar_one()
    return total or 0.0
