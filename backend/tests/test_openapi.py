from fastapi.testclient import TestClient

OPERATIONS = {
    "health",
    "list_claims",
    "get_claim",
    "get_policy",
    "get_claim_history",
    "list_document_sections",
    "get_document_section",
    "get_current_configuration",
    "get_run_configuration",
    "list_configurations",
    "list_recipes",
    "start_round",
    "stop_round",
    "approve_proposal",
    "reject_proposal",
    "list_rounds",
    "get_latest_round",
    "create_round",
    "record_stage",
    "record_proposal",
    "record_decision",
    "record_round_call",
    "get_improver_view",
    "check_evaluation",
    "give_verdict",
    "end_round",
    "get_settings",
    "update_settings",
    "list_handlers",
    "get_me",
    "list_steps",
    "add_step",
    "decide_claim",
    "pay_claim",
    "calculate_payout",
    "sign_off_claim",
    "run_agent",
    "record_model_call",
    "record_work_time",
    "create_dispatch",
    "start_dispatch",
    "create_evaluation",
    "start_evaluation",
    "list_evaluations",
    "get_evaluation",
    "get_latest_dispatch",
    "stop_runs",
    "plan_run",
    "end_run",
    "start_claim",
    "end_attempt",
}


def test_every_operation_is_described_and_has_no_optional_inputs(client: TestClient):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    seen = set()
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            where = f"{method.upper()} {path}"
            assert op.get("operationId"), where
            assert op.get("description"), where
            assert "\n" not in op["description"].strip(), where
            for param in op.get("parameters", []):
                assert param["required"], where
            body = op.get("requestBody")
            if body:
                ref = body["content"]["application/json"]["schema"]["$ref"]
                schema = schemas[ref.rsplit("/", 1)[1]]
                assert set(schema["required"]) == set(schema["properties"]), where
            seen.add(op["operationId"])
    assert seen >= OPERATIONS
