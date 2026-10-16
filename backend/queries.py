"""Reads from the database, as the API's models."""

import json
import sqlite3

from fastapi import HTTPException

from backend.models import (
    ClaimDetail,
    ClaimHistory,
    ClaimSummary,
    Decision,
    Handler,
    Payment,
    Policy,
    SignOff,
    Step,
)

SUMMARY_SQL = """
SELECT c.claim_id, cu.name AS customer_name, c.claim_type, c.summary,
       c.amount_claimed, c.submitted_on, c.state
FROM claims c JOIN customers cu USING (customer_id)
"""


def not_found(what: str, key: str) -> HTTPException:
    """Return the 404 for a missing record."""
    return HTTPException(404, f"No {what} with id {key}")


def list_claims(conn: sqlite3.Connection) -> list[ClaimSummary]:
    """Return every claim, oldest id first."""
    rows = conn.execute(SUMMARY_SQL + " ORDER BY c.claim_id").fetchall()
    return [ClaimSummary.model_validate(dict(row)) for row in rows]


def claim_state(conn: sqlite3.Connection, claim_id: str) -> str:
    """Return a claim's state, or raise a 404."""
    row = conn.execute(
        "SELECT state FROM claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise not_found("claim", claim_id)
    return row["state"]


def get_claim(conn: sqlite3.Connection, claim_id: str) -> ClaimDetail:
    """Return a claim in full, with its decision, payment and sign-off."""
    row = conn.execute(
        "SELECT c.*, cu.name AS customer_name FROM claims c "
        "JOIN customers cu USING (customer_id) WHERE c.claim_id = ?",
        (claim_id,),
    ).fetchone()
    if row is None:
        raise not_found("claim", claim_id)
    detail = dict(row)
    detail["items"] = json.loads(row["items"])
    detail["documents"] = json.loads(row["documents"])
    decision = conn.execute(
        "SELECT * FROM decisions WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    detail["decision"] = Decision.model_validate(dict(decision)) if decision else None
    payment = conn.execute(
        "SELECT * FROM payments WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    detail["payment"] = Payment.model_validate(dict(payment)) if payment else None
    sign_off = conn.execute(
        "SELECT s.handler_id, h.name AS handler_name, s.signed_off_at "
        "FROM sign_offs s JOIN handlers h USING (handler_id) WHERE s.claim_id = ?",
        (claim_id,),
    ).fetchone()
    detail["sign_off"] = SignOff.model_validate(dict(sign_off)) if sign_off else None
    return ClaimDetail.model_validate(detail)


def get_policy(conn: sqlite3.Connection, policy_id: str) -> Policy:
    """Return one policy."""
    row = conn.execute(
        "SELECT data FROM policies WHERE policy_id = ?", (policy_id,)
    ).fetchone()
    if row is None:
        raise not_found("policy", policy_id)
    return Policy.model_validate_json(row["data"])


def get_claim_history(conn: sqlite3.Connection, customer_id: str) -> ClaimHistory:
    """Return a customer's earlier claims, without their contact details."""
    row = conn.execute(
        "SELECT previous_claims FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        raise not_found("customer", customer_id)
    return ClaimHistory(
        customer_id=customer_id, previous_claims=json.loads(row["previous_claims"])
    )


def list_handlers(conn: sqlite3.Connection) -> list[Handler]:
    """Return the claims handlers."""
    rows = conn.execute(
        "SELECT handler_id, name, role FROM handlers ORDER BY handler_id"
    )
    return [Handler.model_validate(dict(row)) for row in rows]


def list_steps(conn: sqlite3.Connection, claim_id: str) -> list[Step]:
    """Return the agent's steps on a claim, in order."""
    claim_state(conn, claim_id)
    rows = conn.execute(
        "SELECT * FROM steps WHERE claim_id = ? ORDER BY id", (claim_id,)
    )
    return [Step.model_validate(dict(row)) for row in rows]
