"""Scoring an evaluation: the agent's answers against the handlers' decisions."""

from dataclasses import dataclass

from backend.models import Measures

WRONGFUL_DECLINE = 1500.0
NEEDLESS_REFERRAL = 50.0
PAYOUT_TOLERANCE = 1.0


@dataclass(frozen=True)
class Answer:
    """What the agent decided on a claim, or None for both when it decided nothing."""

    decision: str | None
    payout: float | None

    @property
    def counted(self) -> tuple[str, float]:
        """A claim the agent did not decide counts as a referral."""
        if self.decision is None:
            return "refer", 0.0
        return self.decision, self.payout or 0.0


def decision_matches(answer: Answer, decision: str) -> bool:
    """True when the agent's decision is the handler's."""
    return answer.counted[0] == decision


def is_right(answer: Answer, decision: str, payout: float) -> bool:
    """True when the decision matches and the payout is within a dollar."""
    return (
        decision_matches(answer, decision)
        and round(abs(answer.counted[1] - payout), 2) <= PAYOUT_TOLERANCE
    )


def mistake_cost(answer: Answer, decision: str, payout: float) -> float:
    """What the agent's answer costs UnderCover, against the handler's decision.

    A wrongful decline costs $1,500, a wrongful approval its payout whether or not
    it was paid, a needless referral $50, and a wrong amount on a right approval
    the difference.
    """
    agent_decision, agent_payout = answer.counted
    if agent_decision == decision:
        return round(abs(agent_payout - payout), 2) if decision == "approve" else 0.0
    if agent_decision == "decline":
        return WRONGFUL_DECLINE
    if agent_decision == "approve":
        return agent_payout
    return NEEDLESS_REFERRAL


def average_grade(grades: list[float | None]) -> float | None:
    """Average the grades that exist. A run with no graded claim has no grade."""
    graded = [g for g in grades if g is not None]
    return sum(graded) / len(graded) if graded else None


@dataclass(frozen=True)
class Totals:
    """What an evaluation's records add up to, besides its answers."""

    cost: float
    claim_seconds: float
    run_seconds: float | None
    grades: list[float | None]
    judge_cost: float


def measures(answers: list[tuple[Answer, str, float]], totals: Totals) -> Measures:
    """Work out the measures of a run from each claim's answer and handler decision."""
    total = len(answers) or 1
    return Measures(
        decision_accuracy=sum(decision_matches(a, d) for a, d, _ in answers) / total,
        accuracy=sum(is_right(a, d, p) for a, d, p in answers) / total,
        cost_of_mistakes=sum(mistake_cost(a, d, p) for a, d, p in answers),
        straight_through=sum(a.counted[0] != "refer" for a, _, _ in answers) / total,
        cost_per_claim=totals.cost / total,
        seconds_per_claim=totals.claim_seconds / total,
        run_seconds=totals.run_seconds,
        grade=average_grade(totals.grades),
        judge_cost=totals.judge_cost,
    )
