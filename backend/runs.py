"""A run of the queue agent: its limit, its to-do list and when it may finish."""

import sqlite3

from fastapi import HTTPException

from backend import queries
from backend.db import now
from backend.models import Run, Todo

SHOWN = 10


def get_run(conn: sqlite3.Connection, run_id: int) -> Run:
    """Return a run with its to-do list, or raise a 404."""
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise queries.not_found("run", str(run_id))
    todos = conn.execute(
        "SELECT text, status FROM todos WHERE run_id = ? ORDER BY position", (run_id,)
    )
    return Run(
        run_id=row["id"],
        limit=row["claim_limit"],
        state=row["state"],
        summary=row["summary"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        todos=[Todo.model_validate(dict(todo)) for todo in todos],
    )


def get_latest(conn: sqlite3.Connection) -> Run | None:
    """Return the most recent run, or None before the first."""
    row = conn.execute("SELECT MAX(id) AS id FROM runs").fetchone()
    return None if row["id"] is None else get_run(conn, row["id"])


def create(conn: sqlite3.Connection, limit: int) -> Run:
    """Start a run, unless one is already going."""
    if conn.execute("SELECT 1 FROM runs WHERE state = 'running'").fetchone():
        raise HTTPException(409, "A run is already going")
    with conn:
        cursor = conn.execute(
            "INSERT INTO runs (claim_limit, state, summary, started_at)"
            " VALUES (?, 'running', '', ?)",
            (limit, now()),
        )
    return get_run(conn, cursor.lastrowid or 0)


def running_run(conn: sqlite3.Connection, run_id: int) -> Run:
    """Return a run that is still going, or raise a 409."""
    run = get_run(conn, run_id)
    if run.state != "running":
        raise HTTPException(409, f"Run {run_id} has already {run.state}")
    return run


def write_todos(conn: sqlite3.Connection, run_id: int, todos: list[Todo]) -> Run:
    """Replace a run's to-do list."""
    running_run(conn, run_id)
    with conn:
        conn.execute("DELETE FROM todos WHERE run_id = ?", (run_id,))
        conn.executemany(
            "INSERT INTO todos VALUES (?, ?, ?, ?)",
            [(run_id, i, todo.text, todo.status) for i, todo in enumerate(todos)],
        )
    return get_run(conn, run_id)


def worked(conn: sqlite3.Connection, run_id: int) -> set[str]:
    """Return the claims a run has started."""
    rows = conn.execute("SELECT claim_id FROM attempts WHERE run_id = ?", (run_id,))
    return {row["claim_id"] for row in rows}


def check_can_start(conn: sqlite3.Connection, run_id: int, claim_id: str) -> None:
    """Refuse to start a new claim once a run has reached its limit."""
    run = running_run(conn, run_id)
    started = worked(conn, run_id)
    if claim_id not in started and len(started) >= run.limit:
        raise HTTPException(
            409,
            f"This run has reached its limit of {run.limit} claims."
            " Do not start another claim",
        )


def missed_claims(conn: sqlite3.Connection, run_id: int) -> list[str]:
    """Return the waiting claims the run should still work."""
    run = get_run(conn, run_id)
    started = worked(conn, run_id)
    waiting = conn.execute(
        "SELECT claim_id FROM claims WHERE state = 'waiting' ORDER BY claim_id"
    )
    ids = [row["claim_id"] for row in waiting]
    if len(started) < run.limit:
        return ids
    return [claim_id for claim_id in ids if claim_id in started]


def end(conn: sqlite3.Connection, run_id: int, state: str, summary: str) -> None:
    """Close a run."""
    with conn:
        conn.execute(
            "UPDATE runs SET state = ?, summary = ?, ended_at = ? WHERE id = ?",
            (state, summary, now(), run_id),
        )


def finish(conn: sqlite3.Connection, run_id: int, summary: str) -> Run:
    """End a run with the agent's summary, unless it has missed a waiting claim."""
    running_run(conn, run_id)
    missed = missed_claims(conn, run_id)
    if missed:
        more = f" and {len(missed) - SHOWN} more" if len(missed) > SHOWN else ""
        raise HTTPException(
            409,
            f"You have missed {', '.join(missed[:SHOWN])}{more}."
            " Work them, then finish the run again",
        )
    end(conn, run_id, "finished", summary)
    return get_run(conn, run_id)


def close_if_running(conn: sqlite3.Connection, run_id: int, error: str | None) -> None:
    """Fail a run whose agent stopped without finishing it."""
    if get_run(conn, run_id).state == "running":
        end(conn, run_id, "failed", error or "The agent stopped without finishing.")


def recover(conn: sqlite3.Connection) -> None:
    """Fail the runs left going by a backend that stopped."""
    for row in conn.execute("SELECT id FROM runs WHERE state = 'running'").fetchall():
        end(conn, row["id"], "failed", "The backend stopped during this run.")
