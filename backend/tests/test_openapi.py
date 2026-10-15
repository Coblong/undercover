from fastapi.testclient import TestClient

OPERATIONS = {
    "health",
    "list_claims",
    "get_claim",
    "get_policy",
    "get_claim_history",
    "get_policy_wording",
    "get_instructions",
    "list_handlers",
    "list_steps",
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
