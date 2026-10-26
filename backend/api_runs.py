"""Routes that start the agents and follow their runs."""

from fastapi import APIRouter, BackgroundTasks

from backend import attempts, runs, work
from backend.deps import ClaimAgent, Db, QueueAgent
from backend.models import (
    FinishRunRequest,
    Outcome,
    Run,
    StartRunRequest,
    TodosRequest,
    WorkResult,
)

router = APIRouter()


@router.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(
    claim_id: str, conn: Db, background: BackgroundTasks, agent: ClaimAgent
) -> Outcome:
    """Start the agent on a waiting claim."""
    outcome = attempts.start_attempt(conn, claim_id)
    background.add_task(work.execute_attempt, claim_id, None, agent)
    return outcome


@router.post("/api/runs", operation_id="start_run", status_code=202)
def start_run(
    body: StartRunRequest, conn: Db, background: BackgroundTasks, agent: QueueAgent
) -> Run:
    """Start the queue agent on the waiting claims, up to a limit."""
    run = runs.create(conn, body.limit)
    background.add_task(work.execute_queue, run.run_id, agent)
    return run


@router.get("/api/runs/latest", operation_id="get_latest_run")
def get_latest_run(conn: Db) -> Run | None:
    """Get the latest run of the queue agent with its to-do list."""
    return runs.get_latest(conn)


@router.put("/api/runs/{run_id}/todos", operation_id="write_todos")
def write_todos(run_id: int, body: TodosRequest, conn: Db) -> Run:
    """Replace the to-do list of a run."""
    return runs.write_todos(conn, run_id, body.todos)


@router.post("/api/runs/{run_id}/claims/{claim_id}/work", operation_id="work_claim")
def work_claim(run_id: int, claim_id: str, conn: Db, agent: ClaimAgent) -> WorkResult:
    """Work one claim in a run with a fresh claim agent and report the result."""
    attempts.start_attempt(conn, claim_id, run_id)
    state = work.execute_attempt(claim_id, run_id, agent)
    return WorkResult(
        claim_id=claim_id, state=state, line=work.result_line(conn, claim_id)
    )


@router.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: int, body: FinishRunRequest, conn: Db) -> Run:
    """End a run with a summary, if no waiting claim has been missed."""
    return runs.finish(conn, run_id, body.summary)
