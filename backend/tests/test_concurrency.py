import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from backend import attempts, decisions, runs
from backend.db import connect, init_db
from backend.models import DecisionRequest, PaymentRequest, SignOffRequest

THREADS = 8
ROUNDS = 50


def decision(kind: str = "approve", payout: float = 420) -> DecisionRequest:
    return DecisionRequest.model_validate(
        {
            "decision": kind,
            "payout": payout,
            "explanation": "Covered.",
            "policy_section": "3. Trip Delay",
        }
    )


def on_own_connection(action: Callable[[sqlite3.Connection], Any]) -> Any:
    conn = connect()
    try:
        return action(conn)
    finally:
        conn.close()


def race(action: Callable[[sqlite3.Connection], Any]) -> list[Any]:
    """Run an action in several threads at once, each on its own connection."""
    barrier = threading.Barrier(THREADS)
    results: list[Any] = []

    def go() -> None:
        barrier.wait()
        try:
            results.append(on_own_connection(action))
        except HTTPException as exc:
            results.append(exc.status_code)
        except Exception as exc:  # anything else is the failure we are testing for
            results.append(repr(exc))

    threads = [threading.Thread(target=go) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def exactly_one_winner(results: list[Any]) -> None:
    refused = [r for r in results if r == 409]
    assert len(refused) == THREADS - 1, results
    assert len(results) == THREADS


@pytest.fixture(params=range(ROUNDS))
def database(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / f"race-{request.param}.db"))
    init_db()


def test_one_payment_wins(database: None):
    on_own_connection(lambda c: decisions.decide(c, "CLM-1001", decision()))
    exactly_one_winner(
        race(lambda c: decisions.pay(c, "CLM-1001", PaymentRequest(amount=420)))
    )
    count = on_own_connection(
        lambda c: c.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    )
    assert count == 1


def test_one_decision_wins(database: None):
    exactly_one_winner(race(lambda c: decisions.decide(c, "CLM-1001", decision())))


def test_one_sign_off_wins(database: None):
    on_own_connection(lambda c: decisions.decide(c, "CLM-1003", decision(payout=1250)))
    exactly_one_winner(
        race(
            lambda c: decisions.sign_off(
                c, "CLM-1003", SignOffRequest(handler_id="HND-01")
            )
        )
    )


def test_one_attempt_starts(database: None):
    exactly_one_winner(race(lambda c: attempts.start_attempt(c, "CLM-1001")))


def test_one_run_starts(database: None):
    exactly_one_winner(race(lambda c: runs.create(c, 5)))


def test_a_run_never_starts_more_claims_than_its_limit(database: None):
    run_id = on_own_connection(lambda c: runs.create(c, 3).run_id)
    claims = iter(f"CLM-10{n:02d}" for n in range(1, 40))
    lock = threading.Lock()

    def start(conn: sqlite3.Connection) -> Any:
        with lock:
            claim_id = next(claims)
        return attempts.start_attempt(conn, claim_id, run_id)

    results = race(start)
    started = [r for r in results if not isinstance(r, int | str)]
    assert len(started) == 3, results
    assert all(r == 409 for r in results if isinstance(r, int | str)), results
