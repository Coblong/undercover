"""A run of the queue agent: its limit, its to-do list and when it may finish."""

from fastapi import HTTPException
from sqlalchemy import Connection, delete, func, insert, select, update

from backend import queries
from backend import tables as t
from backend.db import now, transaction
from backend.models import Run, Todo

SHOWN = 10


def get_run(conn: Connection, run_id: int, lock: bool = False) -> Run:
    """Return a run with its to-do list, or raise a 404. With `lock`, hold its row."""
    query = select(t.runs).where(t.runs.c.id == run_id)
    row = conn.execute(query.with_for_update() if lock else query).first()
    if row is None:
        raise queries.not_found("run", str(run_id))
    todos = conn.execute(
        select(t.todos.c.text, t.todos.c.status)
        .where(t.todos.c.run_id == run_id)
        .order_by(t.todos.c.position)
    ).mappings()
    return Run(
        run_id=row.id,
        limit=row.claim_limit,
        state=row.state,
        summary=row.summary,
        started_at=row.started_at,
        ended_at=row.ended_at,
        todos=[Todo.model_validate(dict(todo)) for todo in todos],
    )


def get_latest(conn: Connection) -> Run | None:
    """Return the most recent run, or None before the first."""
    latest = conn.execute(select(func.max(t.runs.c.id))).scalar_one()
    return None if latest is None else get_run(conn, latest)


def create(conn: Connection, limit: int) -> Run:
    """Start a run, unless one is already going."""
    with transaction(conn):
        if conn.execute(select(t.runs.c.id).where(t.runs.c.state == "running")).first():
            raise HTTPException(409, "A run is already going")
        run_id = conn.execute(
            insert(t.runs)
            .values(claim_limit=limit, state="running", summary="", started_at=now())
            .returning(t.runs.c.id)
        ).scalar_one()
    return get_run(conn, run_id)


def start(conn: Connection, limit: int, live: frozenset[int]) -> Run:
    """Start a run, or take over the one a stopped agent left going.

    A run that no process is working has been stopped. The new run finishes it,
    with its own limit, to-do list and claims. A run still being worked is a 409.
    """
    with transaction(conn):
        stopped = conn.execute(
            select(t.runs.c.id).where(t.runs.c.state == "running").with_for_update()
        ).scalar_one_or_none()
    if stopped is None:
        return create(conn, limit)
    if stopped in live:
        raise HTTPException(409, "A run is already going")
    return get_run(conn, stopped)


def running_run(conn: Connection, run_id: int, lock: bool = False) -> Run:
    """Return a run that is still going, or raise a 409."""
    run = get_run(conn, run_id, lock)
    if run.state != "running":
        raise HTTPException(409, f"Run {run_id} has already {run.state}")
    return run


def write_todos(conn: Connection, run_id: int, todos: list[Todo]) -> Run:
    """Replace a run's to-do list."""
    with transaction(conn):
        running_run(conn, run_id, lock=True)
        conn.execute(delete(t.todos).where(t.todos.c.run_id == run_id))
        if todos:
            conn.execute(
                insert(t.todos),
                [
                    {
                        "run_id": run_id,
                        "position": i,
                        "text": todo.text,
                        "status": todo.status,
                    }
                    for i, todo in enumerate(todos)
                ],
            )
    return get_run(conn, run_id)


def worked(conn: Connection, run_id: int) -> set[str]:
    """Return the claims a run has started."""
    rows = conn.execute(
        select(t.attempts.c.claim_id).where(t.attempts.c.run_id == run_id)
    )
    return {row.claim_id for row in rows}


def check_can_start(conn: Connection, run_id: int, claim_id: str) -> None:
    """Refuse to start a new claim once a run has reached its limit."""
    run = running_run(conn, run_id, lock=True)
    started = worked(conn, run_id)
    if claim_id not in started and len(started) >= run.limit:
        raise HTTPException(
            409,
            f"This run has reached its limit of {run.limit} claims."
            " Do not start another claim",
        )


def missed_claims(conn: Connection, run_id: int) -> list[str]:
    """Return the waiting claims the run should still work."""
    run = get_run(conn, run_id)
    started = worked(conn, run_id)
    waiting = conn.execute(
        select(t.claims.c.claim_id)
        .where(t.claims.c.state == "waiting")
        .order_by(t.claims.c.claim_id)
    )
    ids = [row.claim_id for row in waiting]
    if len(started) < run.limit:
        return ids
    return [claim_id for claim_id in ids if claim_id in started]


def set_ended(conn: Connection, run_id: int, state: str, summary: str) -> None:
    """Close a run. The caller holds the transaction."""
    conn.execute(
        update(t.runs)
        .where(t.runs.c.id == run_id)
        .values(state=state, summary=summary, ended_at=now())
    )


def end(conn: Connection, run_id: int, state: str, summary: str) -> None:
    """Close a run."""
    with transaction(conn):
        set_ended(conn, run_id, state, summary)


def finish(conn: Connection, run_id: int, summary: str) -> Run:
    """End a run with the agent's summary, unless it has missed a waiting claim."""
    with transaction(conn):
        running_run(conn, run_id, lock=True)
        missed = missed_claims(conn, run_id)
        if missed:
            more = f" and {len(missed) - SHOWN} more" if len(missed) > SHOWN else ""
            raise HTTPException(
                409,
                f"You have missed {', '.join(missed[:SHOWN])}{more}."
                " Work them, then finish the run again",
            )
        set_ended(conn, run_id, "finished", summary)
    return get_run(conn, run_id)


def close_if_running(conn: Connection, run_id: int, error: str | None) -> None:
    """Fail a run whose agent stopped without finishing it."""
    with transaction(conn):
        if get_run(conn, run_id, lock=True).state == "running":
            set_ended(
                conn,
                run_id,
                "failed",
                error or "The agent stopped without finishing.",
            )
