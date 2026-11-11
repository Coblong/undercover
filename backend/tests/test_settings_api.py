from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.tests import signing


def values(client: TestClient) -> dict[str, Any]:
    return client.get("/api/settings").json()["values"]


def test_the_settings_start_with_the_labs_values(client: TestClient):
    view = client.get("/api/settings").json()
    assert view["values"] == {
        "approval_limit": 1000,
        "objectives": {"accuracy": 0.6, "grade": 1.0},
        "noise": {
            "accuracy": 0.1,
            "grade": 0.15,
            "cost_per_claim": 0.15,
            "seconds_per_claim": 0.15,
        },
        "tolerance": {
            "accuracy": 0.1,
            "grade": 0.15,
            "cost_per_claim": 1.0,
            "seconds_per_claim": 0.25,
        },
        "spending_limit": 0.5,
        "agents_at_once": 25,
    }
    assert [m["model"] for m in view["models"]] == [
        "openai/gpt-6-luna",
        "google/gemini-3.5-flash-lite",
        "deepseek/deepseek-v4.1-flash",
    ]
    assert view["models"][0]["reasoning_efforts"] == ["none", "low", "medium"]
    assert (view["models"][0]["input_price"], view["models"][0]["output_price"]) == (
        0.1,
        0.5,
    )
    assert {c["changed_by"] for c in view["changes"]} == {None}


def test_a_change_records_who_made_it_and_when(client: TestClient):
    changed = {**values(client), "agents_at_once": 10, "spending_limit": 0.25}
    response = client.put("/api/settings", json=changed)
    assert response.status_code == 200
    view = response.json()
    assert view["values"]["agents_at_once"] == 10
    by_key = {c["key"]: c for c in view["changes"]}
    assert by_key["agents_at_once"]["changed_by"] == "Dana Whitfield"
    assert by_key["spending_limit"]["changed_by"] == "Dana Whitfield"
    assert by_key["approval_limit"]["changed_by"] is None
    assert by_key["agents_at_once"]["changed_at"]


def test_a_setting_that_did_not_change_is_not_recorded_again(client: TestClient):
    before = client.get("/api/settings").json()["changes"]
    client.put("/api/settings", json=values(client))
    assert client.get("/api/settings").json()["changes"] == before


def test_a_handler_who_cannot_sign_off_over_the_limit_cannot_change_a_setting(
    client: TestClient,
):
    original = values(client)
    signing.sign_in_as(client, signing.MARCUS)
    refused = client.put("/api/settings", json={**original, "agents_at_once": 3})
    assert refused.status_code == 403
    assert "Marcus Oyelaran cannot change a setting" in refused.json()["detail"]
    assert values(client) == original
    assert client.get("/api/settings").status_code == 200


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("approval_limit", 0),
        ("agents_at_once", 26),
        ("agents_at_once", 0),
        ("spending_limit", -1),
        ("objectives", {"accuracy": 1.5, "grade": 1}),
        ("objectives", {"accuracy": 0.5, "grade": 4}),
        (
            "noise",
            {"accuracy": -0.1, "grade": 0, "cost_per_claim": 0, "seconds_per_claim": 0},
        ),
    ],
)
def test_a_setting_out_of_range_is_refused(client: TestClient, key: str, bad: Any):
    original = values(client)
    refused = client.put("/api/settings", json={**original, key: bad})
    assert refused.status_code == 422
    assert values(client) == original


def test_changing_a_setting_needs_a_sign_in(client: TestClient):
    original = values(client)
    del client.headers["Authorization"]
    assert client.put("/api/settings", json=original).status_code == 401
