"""P2-AG-06 — reasoning chain JSON schema contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_graphrag.generation.trace import (
    SCHEMA_VERSION,
    Claim,
    CostStats,
    QueryStatus,
    ReasoningChain,
    ReasoningStep,
    ToolCallTrace,
    export_reasoning_chain_schema,
    reasoning_chain_json_schema,
    validate_reasoning_chain,
)


def test_schema_has_core_fields() -> None:
    schema = reasoning_chain_json_schema()
    assert schema["$id"]
    props = schema["properties"]
    for key in (
        "schema_version",
        "query_id",
        "question",
        "route",
        "steps",
        "answer",
        "claims",
        "status",
        "cost",
        "explored_paths",
    ):
        assert key in props


def test_validate_happy_path() -> None:
    chain = ReasoningChain(
        question="Who is CEO of Apex Holdings?",
        route="agentic",
        steps=[
            ReasoningStep(
                hop=1,
                sub_question="Who is CEO of Apex Holdings?",
                tool_calls=[
                    ToolCallTrace(tool="graph_neighbors", reason="entity lookup", hits=["e1"])
                ],
                evidence_ids=["e1"],
                conclusion="Elena Varga",
                critic_action="sufficient",
            )
        ],
        answer="Elena Varga",
        claims=[Claim(text="Elena Varga is CEO", evidence_ids=["e1"])],
        status=QueryStatus.ANSWERED,
        cost=CostStats(llm_calls=0, tokens=0, latency_ms=12),
        explored_paths=["Apex Holdings -[CEO_OF]-> Elena Varga"],
    )
    payload = chain.to_contract_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    again = validate_reasoning_chain(payload)
    assert again.answer == "Elena Varga"
    assert again.status == QueryStatus.ANSWERED


def test_validate_rejects_bad_payload() -> None:
    with pytest.raises(ValueError, match="schema validation failed"):
        validate_reasoning_chain({"question": 123})  # type: ignore[dict-item]


def test_export_schema_file(tmp_path: Path) -> None:
    path = export_reasoning_chain_schema(tmp_path / "reasoning_chain_v1.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"]
    assert "ReasoningChain" in data["title"] or "reasoning" in data["title"].lower()


def test_checked_in_schema_exists() -> None:
    path = Path("configs/schema/reasoning_chain_v1.json")
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "properties" in data
    # Drift guard: re-export matches checked-in structure keys
    live = reasoning_chain_json_schema()
    assert set(live["properties"]) == set(data["properties"])
