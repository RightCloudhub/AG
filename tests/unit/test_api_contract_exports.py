"""Published schemas describe actual REST errors and decoded SSE payloads."""

import importlib.util
from pathlib import Path

import pytest
from test_retrieval_contract import A
from test_retrieval_contract import api as api

from agentic_graphrag.api.public_schema import ErrorEnvelope
from agentic_graphrag.api.stream_contract import EVENT_PAYLOADS


def test_public_schema_security_errors_and_scoped_parameters(api):
    client, _ = api
    schema = client.get("/openapi.json").json()
    ids = []
    for path, methods in schema["paths"].items():
        for operation in methods.values():
            if path.startswith("/v1/"):
                assert operation["security"] == [{"BearerAuth": []}]
                ids.append(operation["operationId"])
    assert len(ids) == len(set(ids))
    get = schema["paths"]["/v1/evidence/{evidence_id}"]["get"]
    assert any(parameter["name"] == "kb_id" for parameter in get["parameters"])
    assert (
        "ErrorEnvelope" in get["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
    )
    invalid = client.post("/v1/query", headers=A, json={"question": ""})
    assert invalid.status_code == 422
    ErrorEnvelope.model_validate(invalid.json())


def test_actual_sse_frames_match_published_payloads_and_unique_terminal(api):
    client, _ = api
    response = client.post("/v1/query/stream", headers=A, json={"question": "hello"})
    query_id = response.headers["x-query-id"]
    events = []
    for frame in response.text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        if "event" not in fields:
            continue
        event = fields["event"]
        payload = EVENT_PAYLOADS[event].model_validate_json(fields["data"])
        assert payload.query_id == query_id
        assert fields["id"] == f"{query_id}:{len(events) + 1}"
        events.append(event)
    assert sum(event in {"answer", "error"} for event in events) == 1
    assert events[-1] == "answer"


def test_compatibility_gate_rejects_removed_fields_and_expanded_enum():
    path = Path(__file__).parents[2] / "scripts" / "contract_compatibility.py"
    spec = importlib.util.spec_from_file_location("contract_compatibility", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check = module.breaking_changes
    original = {"properties": {"route": {"enum": ["fast_path", "agentic"]}}}
    assert check(original, {"properties": {}})
    assert check(original, {"properties": {"route": {"enum": ["fast_path", "agentic", "global"]}}})
    assert not check(original, {**original, "description": "More documentation"})


def test_compatibility_gate_handles_openapi_required_boolean():
    path = Path(__file__).parents[2] / "scripts" / "contract_compatibility.py"
    spec = importlib.util.spec_from_file_location("contract_compatibility", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check = module.breaking_changes
    original = {"requestBody": {"required": True}}
    assert not check(original, original)
    assert check(original, {"requestBody": {"required": False}})
    assert check({"required": False}, {"required": True})
    assert check(
        {"properties": {"required": {"type": "boolean"}}},
        {"properties": {"required": {"type": "string"}}},
    )


@pytest.mark.parametrize(
    "before,after",
    [
        (True, False),
        ({"additionalProperties": True}, {"additionalProperties": False}),
        ({"items": True}, {"items": False}),
        ({"properties": {"allowed": True}}, {"properties": {"allowed": False}}),
        ({"uniqueItems": False}, {"uniqueItems": True}),
    ],
)
def test_compatibility_gate_rejects_changed_boolean_constraints(before, after):
    path = Path(__file__).parents[2] / "scripts" / "contract_compatibility.py"
    spec = importlib.util.spec_from_file_location("contract_compatibility", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.breaking_changes(before, after)
    assert not module.breaking_changes(before, before)
