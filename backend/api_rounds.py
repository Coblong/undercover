"""Routes for the improver: handlers run rounds, and the service records them."""

from contextlib import closing

from fastapi import APIRouter
from sqlalchemy import Connection

from backend import auth, improver_view, rounds
from backend.db import connect
from backend.deps import RoundResumer, RoundStarter, with_db
from backend.models import (
    Check,
    CheckRequest,
    CreateRoundRequest,
    EndRoundRequest,
    ImproverView,
    ProposalDecisionRequest,
    ProposalRequest,
    RejectRequest,
    Round,
    RoundCallRequest,
    StageRequest,
    VerdictRequest,
)

router = APIRouter()


@router.post("/api/rounds", operation_id="start_round", status_code=202)
def start_round(email: auth.SignedIn, starter: RoundStarter) -> Round:
    """Start a round of improvement as a handler who can sign off over the limit."""
    with closing(connect()) as conn:
        handler = auth.senior_handler_for(conn, email, "start a round")
    round_id = starter(handler.handler_id, handler.name)  # the service calls back
    with closing(connect()) as conn:
        return rounds.get_round(conn, round_id)


@router.post("/api/rounds/{round_id}/stop", operation_id="stop_round")
@with_db
def stop_round(round_id: int, email: auth.SignedIn, conn: Connection) -> Round:
    """Stop an open round, leaving the current version as it was."""
    handler = auth.senior_handler_for(conn, email, "stop a round")
    return rounds.stop(conn, round_id, handler)


def decide(
    round_id: int,
    outcome: str,
    email: str,
    reason: str | None,
    resumer: RoundResumer,
) -> Round:
    """Check the handler and the proposal, then have the service record the decision."""
    with closing(connect()) as conn:
        handler = auth.senior_handler_for(
            conn,
            email,
            f"{'approve' if outcome == 'approved' else 'reject'} a proposal",
        )
        rounds.open_round(conn, round_id)
    resumer(round_id, outcome, handler.handler_id, reason)  # the service calls back
    with closing(connect()) as conn:
        return rounds.get_round(conn, round_id)


@router.post(
    "/api/rounds/{round_id}/approve", operation_id="approve_proposal", status_code=202
)
def approve_proposal(
    round_id: int, email: auth.SignedIn, resumer: RoundResumer
) -> Round:
    """Approve the proposal a round is waiting on, and have the improver carry on."""
    return decide(round_id, "approved", email, None, resumer)


@router.post(
    "/api/rounds/{round_id}/reject", operation_id="reject_proposal", status_code=202
)
def reject_proposal(
    round_id: int, body: RejectRequest, email: auth.SignedIn, resumer: RoundResumer
) -> Round:
    """Reject the proposal a round is waiting on, with a reason for the improver."""
    return decide(round_id, "rejected", email, body.reason, resumer)


@router.get("/api/rounds", operation_id="list_rounds")
@with_db
def list_rounds(conn: Connection) -> list[Round]:
    """List the rounds of improvement, newest first."""
    return rounds.list_rounds(conn)


@router.get("/api/rounds/latest", operation_id="get_latest_round")
@with_db
def get_latest_round(conn: Connection) -> Round | None:
    """Get the newest round, or nothing before the first."""
    return rounds.latest_round(conn)


@router.post("/api/improvement/rounds", operation_id="create_round", status_code=201)
@with_db
def create_round(body: CreateRoundRequest, conn: Connection) -> Round:
    """Open a round for a handler, unless one is open."""
    handler = auth.handler_by_id(conn, body.handler_id)
    return rounds.create(conn, handler)


@router.get("/api/improvement/rounds/{round_id}", operation_id="get_round")
@with_db
def get_round(round_id: int, conn: Connection) -> Round:
    """Get one round with its progress, proposal, decisions and cost."""
    return rounds.get_round(conn, round_id)


@router.post("/api/improvement/rounds/{round_id}/stage", operation_id="record_stage")
@with_db
def record_stage(round_id: int, body: StageRequest, conn: Connection) -> Round:
    """Record where a round has got to."""
    return rounds.set_stage(conn, round_id, body)


@router.post(
    "/api/improvement/rounds/{round_id}/proposal", operation_id="record_proposal"
)
@with_db
def record_proposal(round_id: int, body: ProposalRequest, conn: Connection) -> Round:
    """Record the proposal a round waits for a handler to approve."""
    return rounds.record_proposal(conn, round_id, body)


@router.post(
    "/api/improvement/rounds/{round_id}/decision", operation_id="record_decision"
)
@with_db
def record_decision(
    round_id: int, body: ProposalDecisionRequest, conn: Connection
) -> Round:
    """Record a handler's decision on the waiting proposal."""
    return rounds.record_decision(conn, round_id, body)


@router.post(
    "/api/improvement/rounds/{round_id}/calls", operation_id="record_round_call"
)
@with_db
def record_round_call(round_id: int, body: RoundCallRequest, conn: Connection) -> Round:
    """Record one of the improver's model calls with its round."""
    return rounds.record_call(conn, round_id, body)


@router.get("/api/improvement/rounds/{round_id}/view", operation_id="get_improver_view")
@with_db
def get_improver_view(round_id: int, conn: Connection) -> ImproverView:
    """Get what the improver reads: the current version, its scores and its mistakes."""
    return ImproverView.model_validate({"report": improver_view.build(conn, round_id)})


@router.post(
    "/api/improvement/rounds/{round_id}/check", operation_id="check_evaluation"
)
@with_db
def check_evaluation(round_id: int, body: CheckRequest, conn: Connection) -> Check:
    """Say whether a round can afford an evaluation of a whole set."""
    return rounds.check_evaluation(conn, round_id, body)


@router.post("/api/improvement/rounds/{round_id}/verdict", operation_id="give_verdict")
@with_db
def give_verdict(round_id: int, body: VerdictRequest, conn: Connection) -> Round:
    """Keep or roll back the version a round tried, by the rule."""
    return rounds.give_verdict(conn, round_id, body.reason)


@router.post("/api/improvement/rounds/{round_id}/end", operation_id="end_round")
@with_db
def end_round(round_id: int, body: EndRoundRequest, conn: Connection) -> Round:
    """End a round the improver finished or could not carry on."""
    return rounds.end(conn, round_id, body)
