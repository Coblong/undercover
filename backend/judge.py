"""The judge: Jev grades an explanation against the handler's reason.

This is the one place that calls OpenRouter's Decisions endpoint, which is in
alpha. The backend runs it because the backend holds the handlers' decisions, so
the held-out set's reasons never leave it.
"""

import os
from dataclasses import dataclass

from openrouter import OpenRouter
from openrouter.components import DecisionsNoulAnswer, DecisionsScoreAnswer

JEV = "typesafe/jev-1.13"
TIMEOUT_MS = 10_000
YES = 0.5

# Jev numbers the grades by their position in the list, so F is 0 and A is 3.
RUBRIC = {
    "F": "different grounds from the handler's reason, or it contradicts it",
    "C": (
        "partly the same as the handler's reason: one of the handler's grounds or"
        " the section is missing, or it adds a ground the handler did not give"
    ),
    "B": "the same grounds and the same section of the policy wording as the handler",
    "A": (
        "the same grounds and section as the handler's reason, and clearly better"
        " expressed: clearer for the customer, with the section and the figures,"
        " shorter, and no jargon"
    ),
}
GRADES = list(RUBRIC)

QUESTIONS = {
    "grade": {
        "type": "score",
        "instructions": (
            "An expert claims handler decided a travel insurance claim and gave"
            " `handler.reason`, resting on `handler.reference`. Grade how well"
            " `agent.reason` explains the same decision."
        ),
        "criteria": [f"{letter}: {text}" for letter, text in RUBRIC.items()],
    },
    "same_grounds": {
        "type": "noul",
        "instructions": (
            "`agent.reason` gives the same main grounds for the decision as"
            " `handler.reason`"
        ),
    },
    "same_section": {
        "type": "noul",
        "instructions": (
            "`agent.section` is the same section of the policy wording as"
            " `handler.reference`"
        ),
    },
}


@dataclass(frozen=True)
class Case:
    """The handler's decision and the agent's, side by side."""

    handler_decision: str
    handler_reason: str
    handler_reference: str
    agent_decision: str
    agent_reason: str
    agent_section: str


@dataclass(frozen=True)
class Grade:
    """What the judge said about one explanation."""

    grade: float
    letter: str
    same_grounds: bool
    same_section: bool
    cost: float


def server_url() -> str:
    """Return the origin the judge is called at, read from the environment."""
    return os.getenv("OPENROUTER_SERVER_URL") or "https://openrouter.ai"


def grade(case: Case) -> Grade:
    """Ask Jev to grade an explanation against the handler's reason.

    The call times out after 10 seconds and is not retried. The letter is the
    level with the highest probability, not the score rounded.
    """
    state = {
        "handler": {
            "decision": case.handler_decision,
            "reason": case.handler_reason,
            "reference": case.handler_reference,
        },
        "agent": {
            "decision": case.agent_decision,
            "reason": case.agent_reason,
            "section": case.agent_section,
        },
    }
    client = OpenRouter(api_key=os.environ["OPENROUTER_API_KEY"])
    response = client.alpha.decisions.create(
        model=JEV,
        state=state,
        questions=QUESTIONS,
        server_url=server_url(),
        timeout_ms=TIMEOUT_MS,
        retries=None,
    )
    score = response.answers["grade"]
    grounds = response.answers["same_grounds"]
    section = response.answers["same_section"]
    if not (
        isinstance(score, DecisionsScoreAnswer)
        and isinstance(grounds, DecisionsNoulAnswer)
        and isinstance(section, DecisionsNoulAnswer)
        and score.probabilities
    ):
        raise ValueError("The judge did not answer the three questions")
    levels = score.probabilities
    most_likely = max(levels, key=lambda level: levels[level])
    return Grade(
        grade=score.score,
        letter=GRADES[int(most_likely)],
        same_grounds=grounds.noul > YES,
        same_section=section.noul > YES,
        cost=response.usage.cost or 0.0,
    )
