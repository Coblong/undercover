"""The UnderCover backend."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI

from agent.main import run as run_claim_agent
from backend import documents, queries, rules
from backend.db import connect, get_db, init_db
from backend.documents import DocumentName
from backend.models import (
    ClaimDetail,
    ClaimHistory,
    ClaimSummary,
    DecisionRequest,
    Handler,
    Outcome,
    PaymentRequest,
    Policy,
    Section,
    SectionRef,
    SignOffRequest,
    Step,
    StepRequest,
    Text,
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


def execute_run(claim_id: str) -> None:
    """Run the agent on a claim, then tidy up if it left the claim undecided."""
    error = None
    try:
        run_claim_agent(claim_id, f"{claim_id}-{uuid4().hex[:8]}")
    except Exception as exc:  # the run is in the background, so nobody else sees this
        error = f"The agent stopped with an error: {exc}"
    with closing(connect()) as conn:
        rules.end_attempt(conn, claim_id, error)


@app.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(claim_id: str, conn: Db, background: BackgroundTasks) -> Outcome:
    """Start the agent on a waiting claim."""
    outcome = rules.start_attempt(conn, claim_id)
    background.add_task(execute_run, claim_id)
    return outcome
