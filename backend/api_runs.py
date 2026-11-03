"""Routes that start the claim agent and follow dispatches."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from backend import attempts, evaluations, queries, runs, work
from backend.deps import ClaimAgent, Db, DispatchStarter, EvaluationStarter
from backend.models import (
    CreateDispatchRequest,
    CreateEvaluationRequest,
    Dispatch,
    EndAttemptRequest,
    EndRunRequest,
    Evaluation,
    EvaluationDetail,
    Outcome,
    RunClaims,
    StartDispatchRequest,
    StartedClaim,
    StartEvaluationRequest,
    StoppedRuns,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(
    claim_id: str, conn: Db, background: BackgroundTasks, agent: ClaimAgent
) -> Outcome:
    """Start the agent on a waiting claim."""
    if queries.claim_evaluation(conn, claim_id) is not None:
        raise HTTPException(
            409,
            f"Claim {claim_id} is a copy made for an evaluation, so only it runs it",
        )
    resume = queries.claim_state(
        conn, claim_id
    ) == "in_progress" and not work.is_working(claim_id)
    if resume:
        outcome = attempts.carry_on(conn, claim_id)
    else:
        outcome = attempts.start_attempt(conn, claim_id)
    background.add_task(work.execute_attempt, claim_id, None, agent, resume)
    return outcome


@router.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(
    body: StartDispatchRequest, conn: Db, starter: DispatchStarter
) -> Dispatch:
    """Start a dispatch with this many claim agents at once, by asking the service."""
    return runs.get_dispatch(conn, starter(body.concurrency))


@router.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(
    body: StartEvaluationRequest, conn: Db, starter: EvaluationStarter
) -> Evaluation:
    """Start an evaluation of a labelled set, by asking the service."""
    run_id = starter(body.concurrency, body.eval_set, body.tiny)
    return evaluations.get_evaluation(conn, run_id)


@router.get("/api/evaluations", operation_id="list_evaluations")
def list_evaluations(conn: Db) -> list[Evaluation]:
    """List the evaluations with their measures, newest first."""
    return evaluations.list_evaluations(conn)


@router.get("/api/evaluations/{run_id}", operation_id="get_evaluation")
def get_evaluation(run_id: int, conn: Db) -> EvaluationDetail:
    """Get one evaluation with its measures and each claim beside the handler's."""
    return evaluations.get_detail(conn, run_id)


@router.post("/api/runs/evaluation", operation_id="create_evaluation", status_code=201)
def create_evaluation(body: CreateEvaluationRequest, conn: Db) -> Evaluation:
    """Create an evaluation with copies of its claims, unless a run is going."""
    return evaluations.create(conn, body)


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
