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


def claim_columns() -> list[Column]:
    """The columns that describe a claim, which the queue and the held-out set share."""
    return [
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
    ]


CLAIM_FIELDS = [column.name for column in claim_columns()]

claims = Table(
    "claims",
    metadata,
    *claim_columns(),
    Column("state", Text, nullable=False),
    Column("evaluation_id", Integer, ForeignKey("runs.id")),
)
held_out_claims = Table("held_out_claims", metadata, *claim_columns())
answer_key = Table(
    "answer_key",
    metadata,
    Column("eval_set", Text, primary_key=True),
    Column("claim_id", Text, primary_key=True),
    Column("decision", Text, nullable=False),
    Column("payout", Float, nullable=False),
    Column("tiny", Boolean, nullable=False),
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
    Column("email", Text, nullable=False, unique=True),
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
    Column("approval_limit", Float, nullable=False),
)
payments = Table(
    "payments",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("amount", Float, nullable=False),
    Column("paid_at", Text, nullable=False),
    Column("signed_off_by", Text, ForeignKey("handlers.handler_id")),
    Column("signed_off_at", Text),
)
sign_offs = Table(
    "sign_offs",
    metadata,
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("handler_id", Text, ForeignKey("handlers.handler_id"), nullable=False),
    Column("signed_off_at", Text, nullable=False),
)
configurations = Table(
    "configurations",
    metadata,
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("instructions", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("reasoning_effort", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("proposed_by", Text, nullable=False),
    Column("approved_by", Text, ForeignKey("handlers.handler_id")),
    Column("approved_at", Text),
    Column("created_at", Text, nullable=False),
)
current_configuration = Table(
    "current_configuration",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("version", Integer, ForeignKey("configurations.version"), nullable=False),
)
recipes = Table(
    "recipes",
    metadata,
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("recipe", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("proposed_by", Text, nullable=False),
    Column("approved_by", Text, ForeignKey("handlers.handler_id")),
    Column("approved_at", Text),
    Column("created_at", Text, nullable=False),
)
settings = Table(
    "settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", JSON, nullable=False),
)
setting_changes = Table(
    "setting_changes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("key", Text, nullable=False),
    Column("value", JSON, nullable=False),
    Column("changed_by", Text, ForeignKey("handlers.handler_id")),
    Column("changed_at", Text, nullable=False),
    sqlite_autoincrement=True,
)
runs = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("concurrency", Integer, nullable=False),
    Column("model", Text, nullable=False),
    Column("reasoning_effort", Text, nullable=False),
    Column(
        "config_version", Integer, ForeignKey("configurations.version"), nullable=False
    ),
    Column("state", Text, nullable=False),
    Column("eval_set", Text),
    Column("tiny", Boolean),
    Column("started_at", Text, nullable=False),
    Column("ended_at", Text),
    sqlite_autoincrement=True,
)
run_claims = Table(
    "run_claims",
    metadata,
    Column("run_id", Integer, ForeignKey("runs.id"), primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), primary_key=True),
    Column("position", Integer, nullable=False),
    Column("source_id", Text),
    Column("started_at", Text),
)
attempts = Table(
    "attempts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("runs.id")),
    Column("started_at", Text, nullable=False),
    Column("thread_id", Text),
    Column("failed", Boolean),
    sqlite_autoincrement=True,
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

model_calls = Table(
    "model_calls",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("runs.id")),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cost", Float, nullable=False),
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)
work_times = Table(
    "work_times",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("claim_id", Text, ForeignKey("claims.claim_id"), nullable=False),
    Column("run_id", Integer, ForeignKey("runs.id")),
    Column("seconds", Float, nullable=False),
    Column("created_at", Text, nullable=False),
    sqlite_autoincrement=True,
)

Index("model_calls_by_claim", model_calls.c.claim_id)
Index("model_calls_by_run", model_calls.c.run_id)
Index("work_times_by_claim", work_times.c.claim_id)
Index("work_times_by_run", work_times.c.run_id)
Index("steps_by_claim", steps.c.claim_id)
Index("attempts_by_claim", attempts.c.claim_id)
Index("attempts_by_run", attempts.c.run_id)
Index("run_claims_by_claim", run_claims.c.claim_id)
Index("claims_by_evaluation", claims.c.evaluation_id)
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
