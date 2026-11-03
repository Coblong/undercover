"""Routes that start the claim agent and follow dispatches."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend import attempts, queries, runs, work
from backend.deps import ClaimAgent, Db
from backend.models import (
    CreateDispatchRequest,
    Dispatch,
    EndAttemptRequest,
    EndRunRequest,
    Outcome,
    RunClaims,
    StartedClaim,
    StoppedRuns,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(
    claim_id: str, conn: Db, background: BackgroundTasks, agent: ClaimAgent
) -> Outcome:
    """Start the agent on a waiting claim."""
    resume = queries.claim_state(
        conn, claim_id
    ) == "in_progress" and not work.is_working(claim_id)
    if resume:
        outcome = attempts.carry_on(conn, claim_id)
    else:
        outcome = attempts.start_attempt(conn, claim_id)
    background.add_task(work.execute_attempt, claim_id, None, agent, resume)
    return outcome


@router.post("/api/runs/dispatch", operation_id="create_dispatch", status_code=201)
def create_dispatch(body: CreateDispatchRequest, conn: Db) -> Dispatch:
    """Create a dispatch, unless a dispatch or an evaluation is already running."""
    return runs.create_dispatch(conn, body)


@router.get("/api/dispatches/latest", operation_id="get_latest_dispatch")
def get_latest_dispatch(conn: Db) -> Dispatch | None:
    """Get the latest dispatch with its claims, cost, time and summary."""
    return runs.get_latest_dispatch(conn)


@router.post("/api/runs/stop", operation_id="stop_runs")
def stop_runs(conn: Db) -> StoppedRuns:
    """Mark every dispatch and evaluation still running as stopped."""
    return runs.stop_all(conn)


@router.post("/api/runs/{run_id}/plan", operation_id="plan_run")
def plan_run(run_id: int, conn: Db) -> RunClaims:
    """Choose and record the claims a run works, oldest submitted first."""
    return runs.plan(conn, run_id)


@router.post("/api/runs/{run_id}/end", operation_id="end_run")
def end_run(run_id: int, body: EndRunRequest, conn: Db) -> Dispatch:
    """End a dispatch or an evaluation that is running."""
    return runs.end(conn, run_id, body.state)


@router.post("/api/runs/{run_id}/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(run_id: int, claim_id: str, conn: Db) -> StartedClaim:
    """Start a waiting claim in a run, or carry on a claim in progress."""
    return attempts.start_claim(conn, run_id, claim_id)


@router.post("/api/claims/{claim_id}/attempt/end", operation_id="end_attempt")
def end_attempt(claim_id: str, body: EndAttemptRequest, conn: Db) -> Outcome:
    """End the open attempt on a claim, as failed when there is an error."""
    if attempts.open_thread(conn, claim_id) is None:
        raise HTTPException(409, f"Claim {claim_id} has no open attempt to end")
    state = attempts.end_attempt(conn, claim_id, body.error)
    return Outcome(
        claim_id=claim_id, state=state, message=f"The attempt ended: {state}."
    )
