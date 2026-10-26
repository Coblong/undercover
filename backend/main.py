"""The UnderCover backend."""

import logging
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from typing import Annotated
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent.main import run as run_claim_agent
from agent.queue import run_queue as run_queue_agent
from agent.settings import AgentError
from backend import documents, instructions, queries, rules, runs
from backend.db import connect, get_db, init_db
from backend.documents import DocumentName
from backend.instructions import AgentName
from backend.models import (
    ClaimDetail,
    ClaimHistory,
    ClaimSummary,
    DecisionRequest,
    FinishRunRequest,
    Handler,
    Outcome,
    PaymentRequest,
    Policy,
    Run,
    Section,
    SectionRef,
    SignOffRequest,
    StartRunRequest,
    State,
    Step,
    StepRequest,
    Text,
    TodosRequest,
)

Db = Annotated[sqlite3.Connection, Depends(get_db)]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create and seed the database before serving."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    init_db()
    with closing(connect()) as conn:
        rules.recover(conn)
        runs.recover(conn)
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Say what is wrong with a request, without echoing the input back.

    The default answer repeats the input, which is not valid JSON when it is NaN.
    """
    detail = [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(sqlite3.IntegrityError)
def conflict(_: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
    """Report a change the database refused as a conflict, not a crash."""
    return JSONResponse(
        status_code=409, content={"detail": "That conflicts with a change already made"}
    )


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims(conn: Db) -> list[ClaimSummary]:
    """List every claim in the queue with where it stands."""
    return queries.list_claims(conn)


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str, conn: Db) -> ClaimDetail:
    """Get one claim in full, with its decision, payment and sign-off."""
    return queries.get_claim(conn, claim_id)


@app.get("/api/policies/{policy_id}", operation_id="get_policy")
def get_policy(policy_id: str, conn: Db) -> Policy:
    """Get one policy with its plan, add-ons, trip and travelers."""
    return queries.get_policy(conn, policy_id)


@app.get("/api/claims/{claim_id}/claim-history", operation_id="get_claim_history")
def get_claim_history(claim_id: str, conn: Db) -> ClaimHistory:
    """Get the customer's claims filed before this claim, with their outcomes."""
    return queries.get_claim_history(conn, claim_id)


@app.get("/api/documents/{document}/sections", operation_id="list_document_sections")
def list_document_sections(document: DocumentName) -> list[SectionRef]:
    """List the sections of the policy wording or the claims manual."""
    return documents.list_sections(document)


@app.get(
    "/api/documents/{document}/sections/{number}", operation_id="get_document_section"
)
def get_document_section(document: DocumentName, number: int) -> Section:
    """Get one section of the policy wording or the claims manual."""
    return documents.get_section(document, number)


@app.get("/api/instructions/{agent}", operation_id="get_instructions")
def get_instructions(agent: AgentName) -> Text:
    """Get the instructions of the queue agent or the claim agent."""
    return Text(text=instructions.render(agent))


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers(conn: Db) -> list[Handler]:
    """List the claims handlers."""
    return queries.list_handlers(conn)


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, conn: Db) -> list[Step]:
    """List what the agent has done on a claim, step by step."""
    return queries.list_steps(conn, claim_id)


@app.post("/api/claims/{claim_id}/steps", operation_id="add_step")
def add_step(claim_id: str, body: StepRequest, conn: Db) -> Step:
    """Record one thing the agent did on a claim."""
    return rules.add_step(conn, claim_id, body)


@app.post("/api/claims/{claim_id}/decision", operation_id="decide_claim")
def decide_claim(claim_id: str, body: DecisionRequest, conn: Db) -> Outcome:
    """Record the decision on a claim: approve, decline or refer."""
    return rules.decide(conn, claim_id, body)


@app.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim(claim_id: str, body: PaymentRequest, conn: Db) -> Outcome:
    """Pay an approved claim the amount given."""
    return rules.pay(conn, claim_id, body)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(claim_id: str, body: SignOffRequest, conn: Db) -> Outcome:
    """Sign off a payout over the limit as a handler, which pays it."""
    return rules.sign_off(conn, claim_id, body)


def step_text(who: str, exc: Exception) -> str:
    """Say in a step that something stopped, without leaking details into the page.

    The traceback goes to the log. The team sees the kind of error, and the
    message only when the agent itself wrote it for them.
    """
    if isinstance(exc, AgentError):
        return f"{who} stopped with an error: {exc}"
    return f"{who} stopped with an error ({type(exc).__name__})"


def execute_attempt(claim_id: str, run_id: int | None = None) -> State:
    """Run the claim agent on a claim that has been started, then end the attempt."""
    started = time.monotonic()
    error = None
    try:
        run_claim_agent(claim_id, f"{claim_id}-{uuid4().hex[:8]}")
    except Exception as exc:  # the run is in the background, so nobody else sees this
        logger.exception("The agent failed on %s", claim_id)
        error = step_text("The agent", exc)
    with closing(connect()) as conn:
        state = rules.end_attempt(conn, claim_id, error)
    logger.info(
        "Attempt ended: claim=%s run=%s state=%s failed=%s seconds=%.1f",
        claim_id,
        run_id,
        state,
        error is not None or state in ("waiting", "left_for_handler"),
        time.monotonic() - started,
    )
    return state


@app.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(claim_id: str, conn: Db, background: BackgroundTasks) -> Outcome:
    """Start the agent on a waiting claim."""
    outcome = rules.start_attempt(conn, claim_id)
    background.add_task(execute_attempt, claim_id, None)
    return outcome


def execute_queue(run_id: int) -> None:
    """Run the queue agent, then close the run if it left it going."""
    error = None
    try:
        run_queue_agent(run_id)
    except Exception as exc:  # the run is in the background, so nobody else sees this
        logger.exception("The queue agent failed in run %s", run_id)
        error = step_text("The queue agent", exc)
    with closing(connect()) as conn:
        runs.close_if_running(conn, run_id, error)


@app.post("/api/runs", operation_id="start_run", status_code=202)
def start_run(body: StartRunRequest, conn: Db, background: BackgroundTasks) -> Run:
    """Start the queue agent on the waiting claims, up to a limit."""
    run = runs.create(conn, body.limit)
    background.add_task(execute_queue, run.run_id)
    return run


@app.get("/api/runs/latest", operation_id="get_latest_run")
def get_latest_run(conn: Db) -> Run | None:
    """Get the latest run of the queue agent with its to-do list."""
    return runs.get_latest(conn)


@app.put("/api/runs/{run_id}/todos", operation_id="write_todos")
def write_todos(run_id: int, body: TodosRequest, conn: Db) -> Run:
    """Replace the to-do list of a run."""
    return runs.write_todos(conn, run_id, body.todos)


@app.post("/api/runs/{run_id}/claims/{claim_id}/work", operation_id="work_claim")
def work_claim(run_id: int, claim_id: str, conn: Db) -> Outcome:
    """Work one claim in a run with a fresh claim agent and report the result."""
    rules.start_attempt(conn, claim_id, run_id)
    state = execute_attempt(claim_id, run_id)
    return Outcome(
        claim_id=claim_id, state=state, message=rules.result_line(conn, claim_id)
    )


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: int, body: FinishRunRequest, conn: Db) -> Run:
    """End a run with a summary, if no waiting claim has been missed."""
    return runs.finish(conn, run_id, body.summary)
