"""Scoring a version, the rule that keeps or rolls it back, and the estimate.

The rule is the lab's. It works on four measures: accuracy and the grade are
compared as differences, cost and time as fractions of the old value.
"""

from sqlalchemy import Connection, func, select

from backend import configurations, evaluations, settings_store, usage
from backend import tables as t
from backend.models import Objectives, Scores, Thresholds

DIFFERENCES = ("accuracy", "grade")
FRACTIONS = ("cost_per_claim", "seconds_per_claim")
RULE = (
    "A new version is kept when it meets the objectives, at least one measure"
    " improves by more than its noise and none gets worse by more than its tolerance"
)


def compare(
    new: Scores, old: Scores, noise: Thresholds, tolerance: Thresholds
) -> tuple[list[str], list[str]]:
    """Return the measures that got better by more than the noise, and those that
    got worse by more than the tolerance."""
    better: list[str] = []
    worse: list[str] = []
    for name in DIFFERENCES:
        now, before = getattr(new, name), getattr(old, name)
        if now is None or before is None:
            continue
        if now > before + getattr(noise, name):
            better.append(name)
        if now < before - getattr(tolerance, name):
            worse.append(name)
    for name in FRACTIONS:
        now, before = getattr(new, name), getattr(old, name)
        if now < before * (1 - getattr(noise, name)):
            better.append(name)
        if now > before * (1 + getattr(tolerance, name)):
            worse.append(name)
    return better, worse


def keep_or_roll_back(
    new: Scores,
    old: Scores,
    objectives: Objectives,
    noise: Thresholds,
    tolerance: Thresholds,
) -> tuple[bool, str]:
    """Decide from the held-out scores whether to keep a new version, and say why."""
    if new.grade is None:
        return False, "it has no grade"
    missed = [
        name for name in DIFFERENCES if getattr(new, name) < getattr(objectives, name)
    ]
    if missed:
        return False, f"it missed the objectives on {', '.join(missed)}"
    better, worse = compare(new, old, noise, tolerance)
    if worse:
        return False, f"{', '.join(worse)} got worse"
    if not better:
        return False, "no measure improved by more than the noise"
    return True, f"{', '.join(better)} improved"


def version_scores(conn: Connection, version: int, eval_set: str) -> Scores | None:
    """Average the whole-set evaluations that rounds ran on a version."""
    ids = conn.execute(
        select(t.runs.c.id).where(
            t.runs.c.kind == "evaluation",
            t.runs.c.round_id.is_not(None),
            t.runs.c.tiny.is_(False),
            t.runs.c.state == "finished",
            t.runs.c.config_version == version,
            t.runs.c.eval_set == eval_set,
        )
    ).scalars()
    measures = [evaluations.get_evaluation(conn, run_id).measures for run_id in ids]
    if not measures:
        return None
    grades = [m.grade for m in measures if m.grade is not None]
    return Scores(
        runs=len(measures),
        accuracy=sum(m.accuracy for m in measures) / len(measures),
        grade=sum(grades) / len(grades) if grades else None,
        cost_per_claim=sum(m.cost_per_claim for m in measures) / len(measures),
        seconds_per_claim=sum(m.seconds_per_claim for m in measures) / len(measures),
    )


def cost_per_claim_seen(conn: Connection, model: str) -> float | None:
    """Return the highest model cost per claim any evaluation on a model has had."""
    runs = conn.execute(
        select(t.runs.c.id, func.count(t.run_claims.c.claim_id).label("claims"))
        .join(t.run_claims, t.run_claims.c.run_id == t.runs.c.id)
        .where(t.runs.c.kind == "evaluation", t.runs.c.model == model)
        .group_by(t.runs.c.id)
    ).all()
    costs = [usage.run_cost(conn, run.id) / run.claims for run in runs if run.claims]
    return max(costs, default=None)


def estimate(conn: Connection, model: str, claims: int) -> float:
    """Say what an evaluation of this many claims is likely to cost on a model.

    That is the claims times the highest cost per claim seen for the model. For a
    model with no runs it is the current model's, scaled by the larger of the
    ratios of their input and output prices. With nothing seen yet, nothing can be
    estimated, so it is 0.
    """
    seen = cost_per_claim_seen(conn, model)
    if seen is None:
        current = configurations.get_row(conn, configurations.current_version(conn))
        base = cost_per_claim_seen(conn, current["model"])
        if base is None:
            return 0.0
        prices = {m.model: m for m in settings_store.allowed_models()}
        old, new = prices.get(current["model"]), prices.get(model)
        ratios = [1.0]
        if old and new:
            ratios += [
                new.input_price / old.input_price if old.input_price else 1.0,
                new.output_price / old.output_price if old.output_price else 1.0,
            ]
        seen = base * max(ratios)
    return seen * claims
