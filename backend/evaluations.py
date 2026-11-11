"""Evaluations: copies of the labelled claims worked by the agent, and their scores.

This is the one place that reads the handlers' decisions and the held-out claims.
"""

from fastapi import HTTPException
from sqlalchemy import Connection, insert, select

from backend import configurations, queries, scoring, usage
from backend import tables as t
from backend.db import now, transaction
from backend.models import (
    CreateEvaluationRequest,
    Evaluation,
    EvaluationClaim,
    EvaluationDetail,
)
from backend.runs import get_run, seconds_between

ENDED = ("finished", "failed")


def copy_id(run_id: int, source_id: str) -> str:
    """Name the copy of a claim made for an evaluation: EV7-CLM-1004."""
    return f"EV{run_id}-{source_id}"


def sources(conn: Connection, body: CreateEvaluationRequest):
    """Select the labelled claims an evaluation copies, oldest submitted first."""
    table = t.claims if body.eval_set == "evaluation" else t.held_out_claims
    query = (
        select(table)
        .join(t.answer_key, t.answer_key.c.claim_id == table.c.claim_id)
        .where(t.answer_key.c.eval_set == body.eval_set)
        .order_by(table.c.submitted_on, table.c.claim_id)
    )
    if body.eval_set == "evaluation":
        query = query.where(t.claims.c.evaluation_id.is_(None))
    if body.tiny:
        query = query.where(t.answer_key.c.tiny.is_(True))
    return conn.execute(query).mappings().all()


def create(conn: Connection, body: CreateEvaluationRequest) -> Evaluation:
    """Create an evaluation with its own copies of the claims, unless a run is going."""
    with transaction(conn):
        if conn.execute(select(t.runs.c.id).where(t.runs.c.state == "running")).first():
            raise HTTPException(409, "A dispatch or an evaluation is already running")
        version = configurations.get_row(conn, configurations.current_version(conn))
        run_id = conn.execute(
            insert(t.runs)
            .values(
                kind="evaluation",
                concurrency=body.concurrency,
                model=version["model"],
                reasoning_effort=version["reasoning_effort"],
                config_version=version["version"],
                state="running",
                eval_set=body.eval_set,
                tiny=body.tiny,
                started_at=now(),
            )
            .returning(t.runs.c.id)
        ).scalar_one()
        chosen = sources(conn, body)
        conn.execute(
            insert(t.claims),
            [
                {
                    **{name: claim[name] for name in t.CLAIM_FIELDS},
                    "claim_id": copy_id(run_id, claim["claim_id"]),
                    "state": "waiting",
                    "evaluation_id": run_id,
                }
                for claim in chosen
            ],
        )
        conn.execute(
            insert(t.run_claims),
            [
                {
                    "run_id": run_id,
                    "claim_id": copy_id(run_id, claim["claim_id"]),
                    "position": position,
                    "source_id": claim["claim_id"],
                }
                for position, claim in enumerate(chosen)
            ],
        )
    return get_evaluation(conn, run_id)


def results(conn: Connection, run_id: int, eval_set: str) -> list[EvaluationClaim]:
    """Return each claim of an evaluation, the agent's answer beside the handler's."""
    costs = usage.costs_by_claim(conn, run_id)
    times = usage.seconds_by_claim(conn, run_id)
    rows = conn.execute(
        select(
            t.run_claims.c.claim_id,
            t.run_claims.c.source_id,
            t.claims.c.state,
            t.answer_key.c.decision.label("handler_decision"),
            t.answer_key.c.payout.label("handler_payout"),
            t.decisions.c.decision.label("agent_decision"),
            t.decisions.c.payout.label("agent_payout"),
            t.decisions.c.explanation.label("reason"),
        )
        .select_from(t.run_claims)
        .join(t.claims, t.claims.c.claim_id == t.run_claims.c.claim_id)
        .join(
            t.answer_key,
            (t.answer_key.c.claim_id == t.run_claims.c.source_id)
            & (t.answer_key.c.eval_set == eval_set),
        )
        .outerjoin(t.decisions, t.decisions.c.claim_id == t.run_claims.c.claim_id)
        .where(t.run_claims.c.run_id == run_id)
        .order_by(t.run_claims.c.position)
    ).mappings()
    scored = []
    for row in rows:
        answer = scoring.Answer(row["agent_decision"], row["agent_payout"])
        handler = (row["handler_decision"], row["handler_payout"])
        scored.append(
            EvaluationClaim(
                **row,
                decision_matches=scoring.decision_matches(answer, handler[0]),
                right=scoring.is_right(answer, *handler),
                mistake_cost=scoring.mistake_cost(answer, *handler),
                cost=costs.get(row["claim_id"], 0.0),
                seconds=times.get(row["claim_id"], 0.0),
            )
        )
    return scored


def get_evaluation(conn: Connection, run_id: int) -> Evaluation:
    """Return an evaluation with its measures."""
    return Evaluation.model_validate(get_detail(conn, run_id).model_dump())


def get_detail(conn: Connection, run_id: int) -> EvaluationDetail:
    """Return an evaluation with its measures and each claim."""
    run = get_run(conn, run_id)
    if run["kind"] != "evaluation":
        raise queries.not_found("evaluation", str(run_id))
    claims = results(conn, run_id, run["eval_set"])
    answers = [
        (
            scoring.Answer(c.agent_decision, c.agent_payout),
            c.handler_decision,
            c.handler_payout,
        )
        for c in claims
    ]
    run_seconds = (
        seconds_between(run["started_at"], run["ended_at"])
        if run["state"] in ENDED
        else None
    )
    return EvaluationDetail(
        run_id=run_id,
        eval_set=run["eval_set"],
        tiny=run["tiny"],
        concurrency=run["concurrency"],
        model=run["model"],
        reasoning_effort=run["reasoning_effort"],
        config_version=run["config_version"],
        state=run["state"],
        started_at=run["started_at"],
        ended_at=run["ended_at"],
        claim_count=len(claims),
        measures=scoring.measures(
            answers,
            usage.run_cost(conn, run_id),
            usage.run_seconds(conn, run_id),
            run_seconds,
        ),
        results=claims,
    )


def list_evaluations(conn: Connection) -> list[Evaluation]:
    """Return every evaluation, newest first."""
    ids = conn.execute(
        select(t.runs.c.id)
        .where(t.runs.c.kind == "evaluation")
        .order_by(t.runs.c.id.desc())
    )
    return [get_evaluation(conn, row.id) for row in ids]
