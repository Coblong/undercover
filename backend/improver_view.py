"""What the improver is shown: the results it needs, and nothing of the held-out set.

From the evaluation set it reads the claims the agent got wrong and the five right
ones it explained least well, with the handlers' decisions and reasons. From the
held-out set it reads the scores only.
"""

from typing import Any

from sqlalchemy import Connection, select

from backend import configurations, evaluations, improvement, rounds, settings_store
from backend import tables as t
from backend.models import SettingsValues

WEAKEST = 5


def latest_evaluation(conn: Connection, version: int) -> int | None:
    """Return the latest whole evaluation-set run a round made on a version."""
    return conn.execute(
        select(t.runs.c.id)
        .where(
            t.runs.c.kind == "evaluation",
            t.runs.c.round_id.is_not(None),
            t.runs.c.tiny.is_(False),
            t.runs.c.state == "finished",
            t.runs.c.config_version == version,
            t.runs.c.eval_set == "evaluation",
        )
        .order_by(t.runs.c.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def mistakes(conn: Connection, version: int) -> dict[str, list[dict[str, Any]]]:
    """Return the evaluation claims a version got wrong and the weakest right ones."""
    run_id = latest_evaluation(conn, version)
    claims = evaluations.labelled_claims(conn, run_id) if run_id else []
    graded_right = [c for c in claims if c["right"] and c["judge"]["grade"] is not None]
    weakest = sorted(graded_right, key=lambda c: c["judge"]["grade"])[:WEAKEST]
    return {
        "wrong": [c for c in claims if not c["right"]],
        "explained_least_well": weakest,
    }


def earlier_rounds(conn: Connection, round_id: int) -> list[dict[str, Any]]:
    """Return the rounds before this one, and what the handlers rejected in each."""
    earlier = []
    for past in reversed(rounds.list_rounds(conn)):
        if past.id >= round_id:
            continue
        earlier.append(
            {
                "round": past.id,
                "recipe_version": past.recipe_version,
                "version_tried": past.tried_version,
                "reason": past.reason,
                "verdict": past.verdict,
                "why": past.why,
                "held_out_before": past.held_out_before,
                "held_out_after": past.held_out_after,
                "rejected_by_handlers": [
                    {"kind": d.kind, "reason": d.reason}
                    for d in past.decisions
                    if d.outcome == "rejected"
                ],
            }
        )
    return earlier


def build(conn: Connection, round_id: int) -> dict[str, Any]:
    """Return the improver's report on a round, as the lab's read_results does."""
    row = rounds.open_round(conn, round_id)
    settings = SettingsValues.model_validate(row["settings"])
    version = configurations.current_version(conn)
    current = configurations.get_row(conn, version)
    scores = {
        eval_set: improvement.version_scores(conn, version, eval_set)
        for eval_set in ("evaluation", "held_out")
    }
    return {
        "current_version": {
            "version": version,
            "instructions": current["instructions"],
            "model": current["model"],
            "reasoning_effort": current["reasoning_effort"],
        },
        "scores": scores,
        "evaluation_set": mistakes(conn, version),
        "earlier_rounds": earlier_rounds(conn, round_id),
        "objectives_on_the_held_out_set": settings.objectives,
        "noise": settings.noise,
        "tolerance": settings.tolerance,
        "rule": improvement.RULE,
        "spending_left": round(
            max(settings.spending_limit - rounds.spend(conn, round_id), 0.0), 4
        ),
        "models_allowed": settings_store.allowed_models(),
    }
