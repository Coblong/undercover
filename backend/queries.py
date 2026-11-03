"""Reads from the database, as the API's models."""

from fastapi import HTTPException
from sqlalchemy import Connection, func, select, tuple_

from backend import tables as t
from backend.models import (
    ClaimDetail,
    ClaimHistory,
    ClaimSummary,
    Decision,
    Handler,
    Payment,
    Policy,
    PreviousClaim,
    SignOff,
    Step,
)

OUTCOME = {"approve": "approved", "decline": "declined", "refer": "referred"}


def not_found(what: str, key: str) -> HTTPException:
    """Return the 404 for a missing record."""
    return HTTPException(404, f"No {what} with id {key}")


def summaries():
    """Select the columns of a claim in the queue."""
    return select(
        t.claims.c.claim_id,
        t.customers.c.name.label("customer_name"),
        t.claims.c.claim_type,
        t.claims.c.summary,
        t.claims.c.amount_claimed,
        t.claims.c.submitted_on,
        t.claims.c.state,
    ).join(t.customers, t.customers.c.customer_id == t.claims.c.customer_id)


def list_claims(conn: Connection) -> list[ClaimSummary]:
    """Return every claim, oldest id first."""
    rows = conn.execute(summaries().order_by(t.claims.c.claim_id)).mappings()
    return [ClaimSummary.model_validate(dict(row)) for row in rows]


def claim_state(conn: Connection, claim_id: str, lock: bool = False) -> str:
    """Return a claim's state, or raise a 404.

    With `lock`, the claim's row is held until the transaction ends.
    """
    query = select(t.claims.c.state).where(t.claims.c.claim_id == claim_id)
    row = conn.execute(query.with_for_update() if lock else query).first()
    if row is None:
        raise not_found("claim", claim_id)
    return row.state


def get_claim(conn: Connection, claim_id: str) -> ClaimDetail:
    """Return a claim in full, with its decision, payment and sign-off."""
    row = (
        conn.execute(
            select(t.claims, t.customers.c.name.label("customer_name"))
            .join(t.customers, t.customers.c.customer_id == t.claims.c.customer_id)
            .where(t.claims.c.claim_id == claim_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise not_found("claim", claim_id)
    decision = (
        conn.execute(select(t.decisions).where(t.decisions.c.claim_id == claim_id))
        .mappings()
        .first()
    )
    payment = (
        conn.execute(select(t.payments).where(t.payments.c.claim_id == claim_id))
        .mappings()
        .first()
    )
    sign_off = (
        conn.execute(
            select(
                t.sign_offs.c.handler_id,
                t.handlers.c.name.label("handler_name"),
                t.sign_offs.c.signed_off_at,
            )
            .join(t.handlers, t.handlers.c.handler_id == t.sign_offs.c.handler_id)
            .where(t.sign_offs.c.claim_id == claim_id)
        )
        .mappings()
        .first()
    )
    return ClaimDetail.model_validate(
        {
            **row,
            "decision": Decision.model_validate(dict(decision)) if decision else None,
            "payment": Payment.model_validate(dict(payment)) if payment else None,
            "sign_off": SignOff.model_validate(dict(sign_off)) if sign_off else None,
        }
    )


def get_policy(conn: Connection, policy_id: str) -> Policy:
    """Return one policy."""
    row = conn.execute(
        select(t.policies.c.data).where(t.policies.c.policy_id == policy_id)
    ).first()
    if row is None:
        raise not_found("policy", policy_id)
    return Policy.model_validate(row.data)


def get_claim_history(conn: Connection, claim_id: str) -> ClaimHistory:
    """Return the customer's claims filed before this one, with their outcomes.

    That is the earlier claims the customer came with, and every claim in this
    system filed earlier, decided or not.
    """
    claim = conn.execute(
        select(t.claims.c.customer_id, t.claims.c.submitted_on).where(
            t.claims.c.claim_id == claim_id
        )
    ).first()
    if claim is None:
        raise not_found("claim", claim_id)
    seeded = conn.execute(
        select(t.customers.c.previous_claims).where(
            t.customers.c.customer_id == claim.customer_id
        )
    ).one()
    earlier = [PreviousClaim.model_validate(p) for p in seeded.previous_claims]
    here = conn.execute(
        select(
            t.claims.c.claim_id,
            t.claims.c.submitted_on.label("filed_on"),
            t.claims.c.incident_date,
            t.claims.c.claim_type,
            t.claims.c.summary,
            t.decisions.c.decision,
            func.coalesce(t.payments.c.amount, 0).label("amount_paid"),
        )
        .outerjoin(t.decisions, t.decisions.c.claim_id == t.claims.c.claim_id)
        .outerjoin(t.payments, t.payments.c.claim_id == t.claims.c.claim_id)
        .where(
            t.claims.c.customer_id == claim.customer_id,
            tuple_(t.claims.c.submitted_on, t.claims.c.claim_id)
            < tuple_(claim.submitted_on, claim_id),
        )
    ).mappings()
    earlier += [
        PreviousClaim.model_validate(
            {**row, "outcome": OUTCOME.get(row["decision"], "open")}
        )
        for row in here
    ]
    earlier.sort(key=lambda p: p.filed_on)
    return ClaimHistory(customer_id=claim.customer_id, previous_claims=earlier)


def list_handlers(conn: Connection) -> list[Handler]:
    """Return the claims handlers."""
    rows = conn.execute(
        select(t.handlers.c.handler_id, t.handlers.c.name, t.handlers.c.role).order_by(
            t.handlers.c.handler_id
        )
    ).mappings()
    return [Handler.model_validate(dict(row)) for row in rows]


def list_steps(conn: Connection, claim_id: str) -> list[Step]:
    """Return the agent's steps on a claim, in order."""
    claim_state(conn, claim_id)
    rows = conn.execute(
        select(t.steps).where(t.steps.c.claim_id == claim_id).order_by(t.steps.c.id)
    ).mappings()
    return [Step.model_validate(dict(row)) for row in rows]
