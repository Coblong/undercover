"""Rounds of improvement.

A round is one pass of the improver: it reads the results, proposes a change for a
handler to approve, has the change evaluated, and proposes a new recipe. Only one
round is open at a time, and a round works to the settings as they stood when it
started. Nothing the improver proposes takes effect until a handler approves it.
"""

import secrets
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, func, insert, select, update

from backend import configurations, evaluations, improvement, settings_store
from backend import tables as t
from backend.db import now, transaction
from backend.models import (
    Check,
    CheckRequest,
    EndRoundRequest,
    Handler,
    Needs,
    Proposal,
    ProposalDecisionRequest,
    ProposalRequest,
    Round,
    RoundCallRequest,
    RoundDecision,
    Scores,
    SettingsValues,
    StageRequest,
)

MID_WORK = ("first_evaluations", "improver_working", "evaluating", "verdict")
FIRST_EVALUATIONS = {"evaluation": 1, "held_out": 2}
HELD_OUT_RUNS = 2


def get_row(conn: Connection, round_id: int, lock: bool = False):
    """Return a round's row, or raise a 404. With `lock`, hold its row."""
    query = select(t.rounds).where(t.rounds.c.id == round_id)
    row = conn.execute(query.with_for_update() if lock else query).mappings().first()
    if row is None:
        raise HTTPException(404, f"Round {round_id} not found")
    return row


def open_round(conn: Connection, round_id: int, lock: bool = False):
    """Return a round that is still open, or refuse because it is not."""
    row = get_row(conn, round_id, lock)
    if row["state"] != "open":
        raise HTTPException(409, f"Round {round_id} was {row['state']}")
    return row


def spend(conn: Connection, round_id: int) -> float:
    """Return what a round has spent: the improver, its evaluations and the judge."""
    improver = conn.execute(
        select(func.sum(t.round_calls.c.cost)).where(
            t.round_calls.c.round_id == round_id
        )
    ).scalar_one()
    claims = conn.execute(
        select(func.sum(t.model_calls.c.cost))
        .join(t.runs, t.runs.c.id == t.model_calls.c.run_id)
        .where(t.runs.c.round_id == round_id)
    ).scalar_one()
    judge = conn.execute(
        select(func.sum(t.evaluation_grades.c.cost))
        .join(t.runs, t.runs.c.id == t.evaluation_grades.c.run_id)
        .where(t.runs.c.round_id == round_id)
    ).scalar_one()
    return (improver or 0.0) + (claims or 0.0) + (judge or 0.0)


def improver_cost(conn: Connection, round_id: int) -> float:
    """Return what the improver's own model calls cost in a round."""
    total = conn.execute(
        select(func.sum(t.round_calls.c.cost)).where(
            t.round_calls.c.round_id == round_id
        )
    ).scalar_one()
    return total or 0.0


def limit_of(row: Any) -> float:
    """Return the spending limit a round started with."""
    return SettingsValues.model_validate(row["settings"]).spending_limit


def check_can_start(conn: Connection, run_id: int) -> None:
    """Refuse to start a claim in a stopped round's evaluation, or past its limit."""
    round_id = conn.execute(
        select(t.runs.c.round_id).where(t.runs.c.id == run_id)
    ).scalar_one()
    if round_id is None:
        return
    row = open_round(conn, round_id)
    if spend(conn, round_id) >= limit_of(row):
        raise HTTPException(
            409,
            f"Round {round_id} has spent its limit of ${limit_of(row):,.2f},"
            " so no new claim starts",
        )


def create(conn: Connection, handler: Handler) -> Round:
    """Open a round for a handler, unless one is open."""
    with transaction(conn):
        if conn.execute(
            select(t.rounds.c.id).where(t.rounds.c.state == "open")
        ).first():
            raise HTTPException(409, "A round is already open")
        recipe = conn.execute(select(func.max(t.recipes.c.version))).scalar_one()
        round_id = conn.execute(
            insert(t.rounds)
            .values(
                state="open",
                stage="first_evaluations",
                done=0,
                of=0,
                settings=settings_store.current(conn).model_dump(),
                recipe_version=recipe,
                thread_id="pending",
                started_by=handler.handler_id,
                started_at=now(),
            )
            .returning(t.rounds.c.id)
        ).scalar_one()
        conn.execute(
            update(t.rounds)
            .where(t.rounds.c.id == round_id)
            .values(thread_id=f"round-{round_id}-{secrets.token_hex(4)}")
        )
    return get_round(conn, round_id)


def set_stage(conn: Connection, round_id: int, body: StageRequest) -> Round:
    """Record where a round has got to."""
    with transaction(conn):
        open_round(conn, round_id, lock=True)
        conn.execute(
            update(t.rounds)
            .where(t.rounds.c.id == round_id)
            .values(stage=body.stage, done=body.done, of=body.of)
        )
    return get_round(conn, round_id)


def next_version(conn: Connection, kind: str) -> int:
    """Return the number the next version of a configuration or recipe would get."""
    column = t.configurations.c.version if kind == "change" else t.recipes.c.version
    return (conn.execute(select(func.max(column))).scalar_one() or 0) + 1


def record_proposal(conn: Connection, round_id: int, body: ProposalRequest) -> Round:
    """Record the proposal a round is waiting for a handler to approve."""
    with transaction(conn):
        row = open_round(conn, round_id, lock=True)
        if row["pending"] is not None:
            raise HTTPException(409, "A proposal is already waiting for a handler")
        if body.kind == "change":
            if row["tried_version"] is not None:
                raise HTTPException(409, "This round has already tried a version")
            if not body.instructions or not body.model or not body.reasoning_effort:
                raise HTTPException(
                    422, "A change needs instructions, a model and an effort"
                )
            efforts = {
                m.model: m.reasoning_efforts for m in settings_store.allowed_models()
            }
            if body.reasoning_effort not in efforts.get(body.model, []):
                raise HTTPException(
                    422,
                    f"{body.model} at {body.reasoning_effort} is not one of the"
                    " models allowed",
                )
        elif not body.recipe:
            raise HTTPException(422, "A recipe needs its text")
        conn.execute(
            update(t.rounds)
            .where(t.rounds.c.id == round_id)
            .values(
                pending=body.model_dump(),
                stage="change_waiting" if body.kind == "change" else "recipe_waiting",
                done=0,
                of=0,
            )
        )
    return get_round(conn, round_id)


def record_decision(
    conn: Connection, round_id: int, body: ProposalDecisionRequest
) -> Round:
    """Record a handler's decision on the proposal waiting, a version if approved."""
    with transaction(conn):
        row = open_round(conn, round_id, lock=True)
        pending = row["pending"]
        if pending is None:
            raise HTTPException(409, f"Round {round_id} has no proposal waiting")
        handler = conn.execute(
            select(t.handlers.c.handler_id).where(
                t.handlers.c.handler_id == body.handler_id
            )
        ).first()
        if handler is None:
            raise HTTPException(404, f"Handler {body.handler_id} not found")
        version = None
        values: dict[str, Any] = {"pending": None, "stage": "improver_working"}
        if body.outcome == "approved":
            version = next_version(conn, pending["kind"])
            if pending["kind"] == "change":
                conn.execute(
                    insert(t.configurations).values(
                        version=version,
                        instructions=pending["instructions"],
                        model=pending["model"],
                        reasoning_effort=pending["reasoning_effort"],
                        reason=pending["reason"],
                        proposed_by=f"Improver, round {round_id}",
                        round_id=round_id,
                        approved_by=body.handler_id,
                        approved_at=now(),
                        created_at=now(),
                    )
                )
                values |= {"tried_version": version, "reason": pending["reason"]}
            else:
                conn.execute(
                    insert(t.recipes).values(
                        version=version,
                        recipe=pending["recipe"],
                        reason=pending["reason"],
                        proposed_by=f"Improver, round {round_id}",
                        round_id=round_id,
                        approved_by=body.handler_id,
                        approved_at=now(),
                        created_at=now(),
                    )
                )
        conn.execute(
            insert(t.round_decisions).values(
                round_id=round_id,
                kind=pending["kind"],
                outcome=body.outcome,
                handler_id=body.handler_id,
                reason=body.reason,
                version=version,
                decided_at=now(),
            )
        )
        conn.execute(update(t.rounds).where(t.rounds.c.id == round_id).values(**values))
    return get_round(conn, round_id)


def record_call(conn: Connection, round_id: int, body: RoundCallRequest) -> Round:
    """Record one of the improver's model calls with the round."""
    with transaction(conn):
        open_round(conn, round_id, lock=True)
        conn.execute(
            insert(t.round_calls).values(
                round_id=round_id,
                input_tokens=body.input_tokens,
                output_tokens=body.output_tokens,
                cost=body.cost,
                created_at=now(),
            )
        )
    return get_round(conn, round_id)


def stop(conn: Connection, round_id: int, handler: Handler) -> Round:
    """Stop an open round. The current version stays as it is."""
    with transaction(conn):
        open_round(conn, round_id, lock=True)
        conn.execute(
            update(t.rounds)
            .where(t.rounds.c.id == round_id)
            .values(
                state="stopped",
                stage="stopped",
                ended_at=now(),
                stopped_by=handler.handler_id,
                pending=None,
            )
        )
    return get_round(conn, round_id)


def stop_stale(conn: Connection) -> int:
    """Stop the rounds that were mid-work when the service stopped.

    A round waiting for a handler stays open: its pause is saved and carries on.
    """
    with transaction(conn):
        result = conn.execute(
            update(t.rounds)
            .where(t.rounds.c.state == "open", t.rounds.c.stage.in_(MID_WORK))
            .values(state="stopped", stage="stopped", ended_at=now(), pending=None)
        )
    return result.rowcount


def end(conn: Connection, round_id: int, body: EndRoundRequest) -> Round:
    """End a round the improver finished, or one it could not carry on."""
    with transaction(conn):
        row = open_round(conn, round_id, lock=True)
        values: dict[str, Any] = {
            "state": body.state,
            "stage": body.state,
            "ended_at": now(),
            "pending": None,
        }
        if body.why:
            values["why"] = body.why
        if body.state == "finished" and row["tried_version"] and not row["verdict"]:
            values |= {"verdict": "rolled_back", "why": "it was never evaluated"}
        conn.execute(update(t.rounds).where(t.rounds.c.id == round_id).values(**values))
    return get_round(conn, round_id)


def check_evaluation(conn: Connection, round_id: int, body: CheckRequest) -> Check:
    """Say whether a round can afford an evaluation of a whole set."""
    row = open_round(conn, round_id)
    version = configurations.get_row(conn, evaluations.under_test(conn, round_id))
    claims = evaluations.set_size(conn, body.eval_set)
    cost = improvement.estimate(conn, version["model"], claims)
    left = max(limit_of(row) - spend(conn, round_id), 0.0)
    allowed = cost <= left and left > 0
    message = (
        f"Allowed: about ${cost:.4f}, and ${left:.4f} of the spending limit is left"
        if allowed
        else f"Refused: the evaluation could cost about ${cost:.4f} and ${left:.4f}"
        " of the round's spending limit is left"
    )
    return Check(allowed=allowed, estimate=cost, left=left, message=message)


def give_verdict(conn: Connection, round_id: int, reason: str | None) -> Round:
    """Keep or roll back the version a round tried, by the rule, and say why.

    With a reason the version is rolled back for it, as when the round could not
    afford the evaluations.
    """
    with transaction(conn):
        row = open_round(conn, round_id, lock=True)
        tried = row["tried_version"]
        if tried is None:
            raise HTTPException(409, f"Round {round_id} has not tried a version")
        if row["verdict"] is not None:
            raise HTTPException(409, f"Round {round_id} already has its verdict")
        settings = SettingsValues.model_validate(row["settings"])
        current = configurations.current_version(conn)
        before = improvement.version_scores(conn, current, "held_out")
        after = improvement.version_scores(conn, tried, "held_out")
        first = improvement.version_scores(conn, tried, "evaluation")
        if reason:
            keep, why = False, reason
        elif before is None:
            keep, why = False, "the current version has no held-out scores"
        elif after is None or after.runs < HELD_OUT_RUNS or first is None:
            keep, why = False, "its evaluations did not all finish"
        else:
            keep, why = improvement.keep_or_roll_back(
                after, before, settings.objectives, settings.noise, settings.tolerance
            )
        conn.execute(
            update(t.rounds)
            .where(t.rounds.c.id == round_id)
            .values(
                verdict="kept" if keep else "rolled_back",
                why=why,
                held_out_before=before.model_dump() if before else None,
                held_out_after=after.model_dump() if after else None,
                stage="verdict",
            )
        )
        if keep:
            conn.execute(update(t.current_configuration).values(version=tried))
    return get_round(conn, round_id)


def needs(conn: Connection) -> Needs:
    """Return the first evaluations the current version still lacks."""
    current = configurations.current_version(conn)
    have = {}
    for eval_set in FIRST_EVALUATIONS:
        scores = improvement.version_scores(conn, current, eval_set)
        have[eval_set] = scores.runs if scores else 0
    return Needs(
        evaluation=max(FIRST_EVALUATIONS["evaluation"] - have["evaluation"], 0),
        held_out=max(FIRST_EVALUATIONS["held_out"] - have["held_out"], 0),
    )


def proposal_of(conn: Connection, pending: dict[str, Any] | None) -> Proposal | None:
    """Show a waiting proposal with its diff against what it would replace."""
    if pending is None:
        return None
    if pending["kind"] == "change":
        old = configurations.get_row(conn, configurations.current_version(conn))
        return Proposal(
            kind="change",
            version=next_version(conn, "change"),
            reason=pending["reason"],
            model=pending["model"],
            reasoning_effort=pending["reasoning_effort"],
            previous_model=old["model"],
            previous_reasoning_effort=old["reasoning_effort"],
            diff=configurations.diff(old["instructions"], pending["instructions"]),
        )
    latest = conn.execute(
        select(t.recipes.c.recipe).order_by(t.recipes.c.version.desc()).limit(1)
    ).scalar_one()
    return Proposal(
        kind="recipe",
        version=next_version(conn, "recipe"),
        reason=pending["reason"],
        model=None,
        reasoning_effort=None,
        previous_model=None,
        previous_reasoning_effort=None,
        diff=configurations.diff(latest, pending["recipe"]),
    )


def decisions_of(conn: Connection, round_id: int) -> list[RoundDecision]:
    """Return who approved or rejected what in a round, and when."""
    rows = conn.execute(
        select(t.round_decisions, t.handlers.c.name.label("handler"))
        .join(t.handlers, t.handlers.c.handler_id == t.round_decisions.c.handler_id)
        .where(t.round_decisions.c.round_id == round_id)
        .order_by(t.round_decisions.c.id)
    ).mappings()
    return [RoundDecision.model_validate(dict(row)) for row in rows]


def get_round(conn: Connection, round_id: int) -> Round:
    """Return a round with its progress, its proposal, its decisions and its cost."""
    row = get_row(conn, round_id)
    names = {
        r.handler_id: r.name
        for r in conn.execute(select(t.handlers.c.handler_id, t.handlers.c.name))
    }
    total = spend(conn, round_id)
    return Round(
        id=row["id"],
        state=row["state"],
        stage=row["stage"],
        done=row["done"],
        of=row["of"],
        settings=SettingsValues.model_validate(row["settings"]),
        recipe_version=row["recipe_version"],
        started_by=names[row["started_by"]],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        stopped_by=names.get(row["stopped_by"]),
        tried_version=row["tried_version"],
        reason=row["reason"],
        held_out_before=_scores(row["held_out_before"]),
        held_out_after=_scores(row["held_out_after"]),
        verdict=row["verdict"],
        why=row["why"],
        pending=proposal_of(conn, row["pending"]),
        decisions=decisions_of(conn, round_id),
        cost=total,
        improver_cost=improver_cost(conn, round_id),
        spending_left=max(limit_of(row) - total, 0.0),
        needs=needs(conn),
        thread_id=row["thread_id"],
    )


def _scores(value: dict[str, Any] | None) -> Scores | None:
    return Scores.model_validate(value) if value else None


def list_rounds(conn: Connection) -> list[Round]:
    """Return every round, newest first."""
    ids = conn.execute(select(t.rounds.c.id).order_by(t.rounds.c.id.desc())).scalars()
    return [get_round(conn, round_id) for round_id in ids]


def latest_round(conn: Connection) -> Round | None:
    """Return the newest round, or None before the first."""
    latest = conn.execute(select(func.max(t.rounds.c.id))).scalar_one()
    return None if latest is None else get_round(conn, latest)
