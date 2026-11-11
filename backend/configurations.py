"""The claim agent's configuration, kept in versions.

A configuration is the instructions, the model and the reasoning effort. Version 1
is the claim agent as it was built. Each later version is a change a handler
approved, kept or rolled back by the rule in `improvement.py`.
"""

import difflib

from fastapi import HTTPException
from sqlalchemy import Connection, insert, select

from agent import settings as agent_settings
from backend import instructions, settings_store
from backend import tables as t
from backend.db import now
from backend.models import (
    ConfigurationVersion,
    DiffLine,
    RecipeVersion,
    RunConfiguration,
)

DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_EFFORT = "low"
FREE_EFFORT = "default"


def seed(conn: Connection) -> None:
    """Make version 1 from the claim agent's instructions file, and make it current."""
    named = agent_settings.model_name()
    conn.execute(
        insert(t.configurations).values(
            version=1,
            instructions=instructions.template(),
            model=named or DEFAULT_MODEL,
            reasoning_effort=FREE_EFFORT if named else DEFAULT_EFFORT,
            reason="The claim agent as it was built.",
            proposed_by="Seed",
            created_at=now(),
        )
    )
    conn.execute(insert(t.current_configuration).values(id=1, version=1))
    conn.execute(
        insert(t.recipes).values(
            version=1,
            recipe=instructions.HERE.joinpath("recipe_improver.md").read_text(),
            reason="The first recipe: add a rule for each mistake.",
            proposed_by="Seed",
            created_at=now(),
        )
    )


def diff(old: str, new: str) -> list[DiffLine]:
    """Return what changed between two texts, line by line, with a little context."""
    lines: list[DiffLine] = []
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm="", n=2
    ):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            lines.append(DiffLine(kind="context", text="..."))
        elif line.startswith("+"):
            lines.append(DiffLine(kind="add", text=line[1:]))
        elif line.startswith("-"):
            lines.append(DiffLine(kind="remove", text=line[1:]))
        else:
            lines.append(DiffLine(kind="context", text=line[1:]))
    return lines


def current_version(conn: Connection) -> int:
    """Return the number of the current version."""
    return conn.execute(select(t.current_configuration.c.version)).scalar_one()


def get_row(conn: Connection, version: int):
    """Return a version's row, or raise a 404."""
    row = (
        conn.execute(
            select(t.configurations).where(t.configurations.c.version == version)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, f"Version {version} not found")
    return row


def run_configuration(
    conn: Connection, version: int, round_id: int | None = None
) -> RunConfiguration:
    """Return a version as the claim agent works from it, with no scores.

    A run of a round's evaluation also says which round, for tracing.
    """
    row = get_row(conn, version)
    return RunConfiguration(
        version=version,
        instructions=instructions.render(
            row["instructions"], settings_store.approval_limit(conn)
        ),
        model=row["model"],
        reasoning_effort=row["reasoning_effort"],
        round_id=round_id,
    )


def current_run_configuration(conn: Connection) -> RunConfiguration:
    """Return the current version as the claim agent works from it."""
    return run_configuration(conn, current_version(conn))


def run_version(conn: Connection, run_id: int) -> tuple[int, int | None]:
    """Return the version a run used and its round, or raise a 404."""
    row = conn.execute(
        select(t.runs.c.config_version, t.runs.c.round_id).where(t.runs.c.id == run_id)
    ).first()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return row.config_version, row.round_id


def list_versions(conn: Connection) -> list[ConfigurationVersion]:
    """Return every version, newest first, each with its change from the one before."""
    now_current = current_version(conn)
    rows = (
        conn.execute(
            select(
                t.configurations,
                t.handlers.c.name.label("approver"),
            )
            .select_from(
                t.configurations.outerjoin(
                    t.handlers,
                    t.handlers.c.handler_id == t.configurations.c.approved_by,
                )
            )
            .order_by(t.configurations.c.version)
        )
        .mappings()
        .all()
    )
    versions: list[ConfigurationVersion] = []
    before = None
    for row in rows:
        versions.append(
            ConfigurationVersion(
                version=row["version"],
                instructions=row["instructions"],
                model=row["model"],
                reasoning_effort=row["reasoning_effort"],
                reason=row["reason"],
                proposed_by=row["proposed_by"],
                approved_by=row["approver"],
                approved_at=row["approved_at"],
                created_at=row["created_at"],
                current=row["version"] == now_current,
                previous_model=before["model"] if before else None,
                previous_reasoning_effort=(
                    before["reasoning_effort"] if before else None
                ),
                diff=diff(before["instructions"], row["instructions"])
                if before
                else [],
            )
        )
        before = row
    return list(reversed(versions))


def list_recipes(conn: Connection) -> list[RecipeVersion]:
    """Return every version of the improver's recipe, newest first."""
    rows = (
        conn.execute(
            select(t.recipes, t.handlers.c.name.label("approver"))
            .select_from(
                t.recipes.outerjoin(
                    t.handlers, t.handlers.c.handler_id == t.recipes.c.approved_by
                )
            )
            .order_by(t.recipes.c.version)
        )
        .mappings()
        .all()
    )
    versions: list[RecipeVersion] = []
    for position, row in enumerate(rows):
        versions.append(
            RecipeVersion(
                version=row["version"],
                recipe=row["recipe"],
                reason=row["reason"],
                proposed_by=row["proposed_by"],
                approved_by=row["approver"],
                approved_at=row["approved_at"],
                created_at=row["created_at"],
                current=position == len(rows) - 1,
                diff=diff(rows[position - 1]["recipe"], row["recipe"])
                if position
                else [],
            )
        )
    return list(reversed(versions))
