"""Run the agent on one claim: uv run -m agent --help"""

import argparse
import json
import time

from agent.main import call
from agent.settings import load_environment


def report(claim_id: str) -> None:
    """Print how a claim stands."""
    claim = json.loads(call("GET", f"/api/claims/{claim_id}"))
    decision = claim["decision"]
    outcome = (
        f"{decision['decision']} {decision['payout']:.2f}"
        if decision
        else "no decision"
    )
    print(f"{claim_id}: {claim['state']}, {outcome}")


def work(claim_id: str) -> None:
    """Ask the backend to run the agent on a claim, wait, and report the result."""
    started = call("POST", f"/api/claims/{claim_id}/run")
    if started.startswith("Error"):
        print(f"{claim_id}: {started}")
        return
    while json.loads(call("GET", f"/api/claims/{claim_id}"))["state"] == "in_progress":
        time.sleep(2)
    report(claim_id)


def main() -> None:
    """Read the command line and run the agent."""
    parser = argparse.ArgumentParser(
        description="Run the UnderCover Agent through the backend on one claim,"
        " carrying it on if it was stopped. Dispatches start from the dashboard."
    )
    parser.add_argument("claim_id", help="a claim id such as CLM-1001")
    args = parser.parse_args()
    load_environment()
    work(args.claim_id)


if __name__ == "__main__":
    main()
