"""The claim agent's instructions, with UnderCover's own numbers filled in."""

from pathlib import Path

from backend.db import load

HERE = Path(__file__).resolve().parent


def template() -> str:
    """Return version 1's instructions, with their {placeholders}."""
    return (HERE / "instructions_claim.md").read_text()


def render(stored: str, approval_limit: float) -> str:
    """Fill the four placeholders in a version's instructions.

    Only those four names are replaced, so any other braces a version holds stay.
    """
    company = load("company.json")
    review = company["repeat_claim_review"]
    filled = {
        "{approval_limit}": f"${approval_limit:,.0f}",
        "{filing_days}": str(company["claim_filing_window_days"]),
        "{repeat_claims}": str(review["claims"]),
        "{repeat_months}": str(review["within_months"]),
    }
    for name, value in filled.items():
        stored = stored.replace(name, value)
    return stored
