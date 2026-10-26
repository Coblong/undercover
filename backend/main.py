"""The UnderCover backend."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI

from agent.main import run as run_claim_agent
from agent.queue import run_queue as run_queue_agent
from backend import documents, queries, rules, runs
from backend.db import connect, get_db, init_db
from backend.documents import DocumentName
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

HERE = Path(__file__).resolve().parent
AgentName = Literal["queue", "claim"]

Db = Annotated[sqlite3.Connection, Depends(get_db)]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create and seed the database before serving."""
    init_db()
    with closing(connect()) as conn:
        rules.recover(conn)
        runs.recover(conn)
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


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


@app.get("/api/customers/{customer_id}/claim-history", operation_id="get_claim_history")
def get_claim_history(customer_id: str, conn: Db) -> ClaimHistory:
    """Get a customer's earlier claims."""
    return queries.get_claim_history(conn, customer_id)


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
    return Text(text=(HERE / f"instructions_{agent}.md").read_text())


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


def execute_attempt(claim_id: str) -> State:
    """Run the claim agent on a claim that has been started, then end the attempt."""
    error = None
    try:
        run_claim_agent(claim_id, f"{claim_id}-{uuid4().hex[:8]}")
    except Exception as exc:  # the run is in the background, so nobody else sees this
        error = f"The agent stopped with an error: {exc}"
    with closing(connect()) as conn:
        return rules.end_attempt(conn, claim_id, error)


@app.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(claim_id: str, conn: Db, background: BackgroundTasks) -> Outcome:
    """Start the agent on a waiting claim."""
    outcome = rules.start_attempt(conn, claim_id)
    background.add_task(execute_attempt, claim_id)
    return outcome


def execute_queue(run_id: int) -> None:
    """Run the queue agent, then close the run if it left it going."""
    error = None
    try:
        run_queue_agent(run_id)
    except Exception as exc:  # the run is in the background, so nobody else sees this
        error = f"The queue agent stopped with an error: {exc}"
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
    runs.check_can_start(conn, run_id, claim_id)
    rules.start_attempt(conn, claim_id, run_id)
    state = execute_attempt(claim_id)
    return Outcome(
        claim_id=claim_id, state=state, message=rules.result_line(conn, claim_id)
    )


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: int, body: FinishRunRequest, conn: Db) -> Run:
    """End a run with a summary, if no waiting claim has been missed."""
    return runs.finish(conn, run_id, body.summary)
