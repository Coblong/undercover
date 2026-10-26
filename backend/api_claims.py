"""Routes for claims, documents and what the team and the agent read and write."""

from fastapi import APIRouter

from backend import attempts, decisions, documents, instructions, queries
from backend.deps import Db
from backend.instructions import AgentName
from backend.models import (
    ClaimDetail,
    ClaimHistory,
    ClaimSummary,
    DecisionRequest,
    DocumentName,
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

router = APIRouter()


@router.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@router.get("/api/claims", operation_id="list_claims")
def list_claims(conn: Db) -> list[ClaimSummary]:
    """List every claim in the queue with where it stands."""
    return queries.list_claims(conn)


@router.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str, conn: Db) -> ClaimDetail:
    """Get one claim in full, with its decision, payment and sign-off."""
    return queries.get_claim(conn, claim_id)


@router.get("/api/policies/{policy_id}", operation_id="get_policy")
def get_policy(policy_id: str, conn: Db) -> Policy:
    """Get one policy with its plan, add-ons, trip and travelers."""
    return queries.get_policy(conn, policy_id)


@router.get("/api/claims/{claim_id}/claim-history", operation_id="get_claim_history")
def get_claim_history(claim_id: str, conn: Db) -> ClaimHistory:
    """Get the customer's claims filed before this claim, with their outcomes."""
    return queries.get_claim_history(conn, claim_id)


@router.get("/api/documents/{document}/sections", operation_id="list_document_sections")
def list_document_sections(document: DocumentName) -> list[SectionRef]:
    """List the sections of the policy wording or the claims manual."""
    return documents.list_sections(document)


@router.get(
    "/api/documents/{document}/sections/{number}", operation_id="get_document_section"
)
def get_document_section(document: DocumentName, number: int) -> Section:
    """Get one section of the policy wording or the claims manual."""
    return documents.get_section(document, number)


@router.get("/api/instructions/{agent}", operation_id="get_instructions")
def get_instructions(agent: AgentName) -> Text:
    """Get the instructions of the queue agent or the claim agent."""
    return Text(text=instructions.render(agent))


@router.get("/api/handlers", operation_id="list_handlers")
def list_handlers(conn: Db) -> list[Handler]:
    """List the claims handlers."""
    return queries.list_handlers(conn)


@router.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, conn: Db) -> list[Step]:
    """List what the agent has done on a claim, step by step."""
    return queries.list_steps(conn, claim_id)


@router.post("/api/claims/{claim_id}/steps", operation_id="add_step")
def add_step(claim_id: str, body: StepRequest, conn: Db) -> Step:
    """Record one thing the agent did on a claim."""
    return attempts.add_step(conn, claim_id, body)


@router.post("/api/claims/{claim_id}/decision", operation_id="decide_claim")
def decide_claim(claim_id: str, body: DecisionRequest, conn: Db) -> Outcome:
    """Record the decision on a claim: approve, decline or refer."""
    return decisions.decide(conn, claim_id, body)


@router.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim(claim_id: str, body: PaymentRequest, conn: Db) -> Outcome:
    """Pay an approved claim the amount given."""
    return decisions.pay(conn, claim_id, body)


@router.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(claim_id: str, body: SignOffRequest, conn: Db) -> Outcome:
    """Sign off a payout over the limit as a handler, which pays it."""
    return decisions.sign_off(conn, claim_id, body)
