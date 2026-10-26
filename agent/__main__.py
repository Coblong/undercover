"""Run the agent on the waiting claims, or on one claim: uv run -m agent --help"""

import argparse
import json
import time

from agent.main import call


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


def waiting_claims() -> list[str]:
    """Return the ids of the waiting claims."""
    claims = json.loads(call("GET", "/api/claims"))
    return [c["claim_id"] for c in claims if c["state"] == "waiting"]


def work_queue(limit: int | None) -> None:
    """Start a run on the waiting claims, wait for it, and report each claim."""
    before = waiting_claims()
    if not before:
        print("No claims are waiting.")
        return
    started = call("POST", "/api/runs", {"limit": limit or len(before)})
    if started.startswith("Error"):
        print(started)
        return
    while (run := json.loads(call("GET", "/api/runs/latest")))["state"] == "running":
        time.sleep(2)
    still_waiting = set(waiting_claims())
    for claim_id in before:
        if claim_id not in still_waiting:
            report(claim_id)
    print(f"Run {run['state']}: {run['summary']}")


def main() -> None:
    """Read the command line and run the agent."""
    parser = argparse.ArgumentParser(
        description="Run the UnderCover Agent through the backend."
        " With no claim id it works the waiting claims as one run."
    )
    parser.add_argument("claim_id", nargs="?", help="a claim id such as CLM-1001")
    parser.add_argument(
        "--limit", type=int, help="work at most this many claims in the run"
    )
    args = parser.parse_args()
    if args.claim_id and args.limit:
        parser.error("give a claim id or --limit, not both")
    if args.claim_id:
        work(args.claim_id)
    else:
        work_queue(args.limit)


if __name__ == "__main__":
    main()
