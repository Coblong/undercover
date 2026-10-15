"""Run the agent on one claim, or on every waiting claim: uv run -m agent --help"""

import argparse
import json
import time

from agent.main import call


def work(claim_id: str) -> None:
    """Ask the backend to run the agent on a claim, wait, and report the result."""
    started = call("POST", f"/api/claims/{claim_id}/run")
    if started.startswith("Error"):
        print(f"{claim_id}: {started}")
        return
    while (claim := json.loads(call("GET", f"/api/claims/{claim_id}")))[
        "state"
    ] == "in_progress":
        time.sleep(2)
    decision = claim["decision"]
    outcome = (
        f"{decision['decision']} {decision['payout']:.2f}"
        if decision
        else "no decision"
    )
    print(f"{claim_id}: {claim['state']}, {outcome}")


def main() -> None:
    """Read the command line and run the agent."""
    parser = argparse.ArgumentParser(
        description="Run the UnderCover Agent through the backend."
    )
    parser.add_argument("claim_id", nargs="?", help="a claim id such as CLM-1001")
    parser.add_argument(
        "--all", action="store_true", help="run every waiting claim in turn"
    )
    args = parser.parse_args()
    if args.all:
        for claim in json.loads(call("GET", "/api/claims")):
            if claim["state"] == "waiting":
                work(claim["claim_id"])
    elif args.claim_id:
        work(args.claim_id)
    else:
        parser.error("give a claim id or --all")


if __name__ == "__main__":
    main()
