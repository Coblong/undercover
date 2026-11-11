"""Routes for claims, documents and what the team and the agent read and write."""

from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import Connection

from backend import (
    attempts,
    auth,
    configurations,
    decisions,
    documents,
    improvement,
    payout,
    queries,
    settings_store,
    usage,
    work,
)
from backend.deps import ClaimAgent, with_db
from backend.models import (
    ClaimDetail,
    ClaimHistory,
    ClaimSummary,
    ConfigurationVersion,
    DecisionRequest,
    DocumentName,
    Handler,
    ModelCallRequest,
    Outcome,
    PaymentRequest,
    Policy,
    RecipeVersion,
    RunConfiguration,
    Section,
    SectionRef,
    SettingsValues,
    SettingsView,
    Step,
    StepRequest,
    WorkTimeRequest,
)
from backend.payout import Payout, PayoutRequest

router = APIRouter()


@router.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@router.get("/api/claims", operation_id="list_claims")
@with_db
def list_claims(conn: Connection) -> list[ClaimSummary]:
    """List every claim in the queue with where it stands."""
    return queries.list_claims(conn)


@router.get("/api/claims/{claim_id}", operation_id="get_claim")
@with_db
def get_claim(claim_id: str, conn: Connection) -> ClaimDetail:
    """Get one claim in full, with its decision, payment and sign-off."""
    return queries.get_claim(conn, claim_id)


@router.get("/api/policies/{policy_id}", operation_id="get_policy")
@with_db
def get_policy(policy_id: str, conn: Connection) -> Policy:
    """Get one policy with its plan, add-ons, trip and travelers."""
    return queries.get_policy(conn, policy_id)


@router.get("/api/claims/{claim_id}/claim-history", operation_id="get_claim_history")
@with_db
def get_claim_history(claim_id: str, conn: Connection) -> ClaimHistory:
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


@router.get("/api/me", operation_id="get_me")
@with_db
def get_me(email: auth.SignedIn, conn: Connection) -> Handler:
    """Get the signed-in handler."""
    return auth.handler_for(conn, email)


@router.get("/api/handlers", operation_id="list_handlers")
@with_db
def list_handlers(conn: Connection) -> list[Handler]:
    """List the claims handlers."""
    return queries.list_handlers(conn)


@router.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
@with_db
def list_steps(claim_id: str, conn: Connection) -> list[Step]:
    """List what the agent has done on a claim, step by step."""
    return queries.list_steps(conn, claim_id)


@router.post("/api/claims/{claim_id}/steps", operation_id="add_step")
@with_db
def add_step(claim_id: str, body: StepRequest, conn: Connection) -> Step:
    """Record one thing the agent did on a claim."""
    return attempts.add_step(conn, claim_id, body)


@router.post("/api/claims/{claim_id}/decision", operation_id="decide_claim")
@with_db
def decide_claim(claim_id: str, body: DecisionRequest, conn: Connection) -> Outcome:
    """Record the decision on a claim: approve, decline or refer."""
    return decisions.decide(conn, claim_id, body)


@router.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
@with_db
def pay_claim(claim_id: str, body: PaymentRequest, conn: Connection) -> Outcome:
    """Pay an approved claim the amount given."""
    return decisions.pay(conn, claim_id, body)


@router.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
@with_db
def sign_off_claim(
    claim_id: str,
    email: auth.SignedIn,
    conn: Connection,
    background: BackgroundTasks,
    agent: ClaimAgent,
) -> Outcome:
    """Sign off a payout over the limit as the signed-in handler, and hand it on."""
    handler = auth.handler_for(conn, email)
    outcome = decisions.sign_off(conn, claim_id, handler)
    background.add_task(work.execute_attempt, claim_id, None, agent, True)
    return outcome


@router.post("/api/claims/{claim_id}/payout", operation_id="calculate_payout")
@with_db
def calculate_payout(claim_id: str, body: PayoutRequest, conn: Connection) -> Payout:
    """Work out the payout on an approved claim with UnderCover's payout rules."""
    claim = queries.get_claim(conn, claim_id)
    policy = queries.get_policy(conn, claim.policy_id)
    return payout.work_out_payout(
        policy.model_dump(), body, settings_store.approval_limit(conn)
    )


@router.post("/api/claims/{claim_id}/model-calls", operation_id="record_model_call")
@with_db
def record_model_call(
    claim_id: str, body: ModelCallRequest, conn: Connection
) -> Outcome:
    """Record the tokens and the cost of one model call on a claim."""
    return usage.record_model_call(conn, claim_id, body)


@router.post("/api/claims/{claim_id}/work-times", operation_id="record_work_time")
@with_db
def record_work_time(claim_id: str, body: WorkTimeRequest, conn: Connection) -> Outcome:
    """Record the seconds the agent spent working on a claim."""
    return usage.record_work_time(conn, claim_id, body)


@router.get("/api/configurations", operation_id="list_configurations")
@with_db
def list_configurations(conn: Connection) -> list[ConfigurationVersion]:
    """List every version of the claim agent's configuration, newest first."""
    return [
        version.model_copy(
            update={
                "held_out": improvement.version_scores(
                    conn, version.version, "held_out"
                ),
                "evaluation": improvement.version_scores(
                    conn, version.version, "evaluation"
                ),
            }
        )
        for version in configurations.list_versions(conn)
    ]


@router.get("/api/recipes", operation_id="list_recipes")
@with_db
def list_recipes(conn: Connection) -> list[RecipeVersion]:
    """List every version of the improver's recipe, newest first."""
    return configurations.list_recipes(conn)


@router.get("/api/configuration/current", operation_id="get_current_configuration")
@with_db
def get_current_configuration(conn: Connection) -> RunConfiguration:
    """Get the current version of the claim agent's configuration, with no scores."""
    return configurations.current_run_configuration(conn)


@router.get("/api/runs/{run_id}/configuration", operation_id="get_run_configuration")
@with_db
def get_run_configuration(run_id: int, conn: Connection) -> RunConfiguration:
    """Get the version of the claim agent's configuration a run used, with no scores."""
    version, round_id = configurations.run_version(conn, run_id)
    return configurations.run_configuration(conn, version, round_id)


@router.get("/api/settings", operation_id="get_settings")
@with_db
def get_settings(conn: Connection) -> SettingsView:
    """Get the platform's settings and the models the claim agent may use."""
    return settings_store.view(conn)


@router.put("/api/settings", operation_id="update_settings")
@with_db
def update_settings(
    body: SettingsValues, email: auth.SignedIn, conn: Connection
) -> SettingsView:
    """Change the platform's settings as a handler who can sign off over the limit."""
    handler = auth.senior_handler_for(conn, email, "change a setting")
    return settings_store.update_settings(conn, handler, body)
