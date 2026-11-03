"""The tables, defined once for SQLite and Postgres."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
)

metadata = MetaData()

claims = Table(
    "claims",
    metadata,
    Column("claim_id", Text, primary_key=True),
    Column("policy_id", Text, nullable=False),
    Column("customer_id", Text, nullable=False),
    Column("claim_type", Text, nullable=False),
    Column("submitted_on", Text, nullable=False),
    Column("incident_date", Text, nullable=False),
    Column("incident_location", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("story", Text, nullable=False),
    Column("items", JSON, nullable=False),
    Column("documents", JSON, nullable=False),
    Column("amount_claimed", Float, nullable=False),
    Column("state", Text, nullable=False),
)
policies = Table(
    "policies",
    metadata,
    Column("policy_id", Text, primary_key=True),
    Column("data", JSON, nullable=False),
)
customers = Table(
    "customers",
    metadata,
    Column("customer_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("previous_claims", JSON, nullable=False),
)
handlers = Table(
    "handlers",
    metadata,
    Column("handler_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("can_approve_over_limit", Boolean, nullable=False),
)
decisions = Table(
    "decisions",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("decision", Text, nullable=False),
    Column("payout", Float, nullable=False),
    Column("explanation", Text, nullable=False),
    Column("policy_section", Text, nullable=False),
    Column("decided_at", Text, nullable=False),
)
payments = Table(
    "payments",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("amount", Float, nullable=False),
    Column("paid_at", Text, nullable=False),
)
sign_offs = Table(
    "sign_offs",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("handler_id", Text, ForeignKey("handlers.handler_id"), nullable=False),
    Column("signed_off_at", Text, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("claim_limit", Integer, nullable=False),
    Column("state", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("started_at", Text, nullable=False),
    Column("ended_at", Text),
    sqlite_autoincrement=True,
)
attempts = Table(
    "attempts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("runs.id")),
    Column("started_at", Text, nullable=False),
    Column("failed", Boolean),
    sqlite_autoincrement=True,
)
todos = Table(
    "todos",
    metadata,
    Column("run_id", Integer, ForeignKey("runs.id"), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("text", Text, nullable=False),
    Column("status", Text, nullable=False),
)
steps = Table(
    "steps",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

Index("steps_by_claim", steps.c.claim_id)
Index("attempts_by_claim", attempts.c.claim_id)
Index("attempts_by_run", attempts.c.run_id)
Index(
    "one_open_attempt",
    attempts.c.claim_id,
    unique=True,
    sqlite_where=attempts.c.failed.is_(None),
    postgresql_where=attempts.c.failed.is_(None),
)
Index(
    "one_running_run",
    runs.c.state,
    unique=True,
    sqlite_where=runs.c.state == "running",
    postgresql_where=runs.c.state == "running",
)
