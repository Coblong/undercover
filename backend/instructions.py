"""The agents' instructions, with UnderCover's own numbers filled in."""

from pathlib import Path
from typing import Literal

from backend.db import load

AgentName = Literal["queue", "claim"]

HERE = Path(__file__).resolve().parent


def render(agent: AgentName) -> str:
    """Return an agent's instructions, with the numbers from `data/company.json`."""
    company = load("company.json")
    review = company["repeat_claim_review"]
    return (
        (HERE / f"instructions_{agent}.md")
        .read_text()
        .format(
            approval_limit=f"${company['payout_approval_limit']:,.0f}",
            filing_days=company["claim_filing_window_days"],
            repeat_claims=review["claims"],
            repeat_months=review["within_months"],
        )
    )
