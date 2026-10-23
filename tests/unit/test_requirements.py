"""Hard transitions and scheduling cannot be delegated to a model."""

import pytest
from pydantic import ValidationError

from agentic_graphrag.agent.register_invariants import apply_invariants, schedule
from agentic_graphrag.agent.register_search import query_key
from agentic_graphrag.agent.requirements import Register, Requirement, compose, diff
from agentic_graphrag.config_register import RequirementRegisterConfig
from agentic_graphrag.core.evidence import Candidate


def item(text="Who owns Acme?", **kwargs):
    return Requirement(text=text, **kwargs)


def evidence(*ids):
    return {
        rid: Candidate(id=rid, source="vector", content="Acme is owned by Bridge.") for rid in ids
    }


def test_omission_does_not_delete_and_inputs_are_not_mutated():
    old = Register(question="q", items={"r": item(status="active", passages={"c1"})})
    new = Register(question="changed")
    merged = apply_invariants(new, old, evidence=evidence("c1"))
    assert merged.question == "q"
    assert merged.items["r"].passages == {"c1"}
    merged.items["r"].passages.add("c2")
    assert old.items["r"].passages == {"c1"}
    assert new.items == {}


def test_passages_append_and_fabricated_ids_cannot_close_need():
    old = Register(question="q", items={"r": item(status="active", passages={"c1"})})
    new = Register(question="q", items={"r": item(status="resolved", passages={"c2", "fake"})})
    merged = apply_invariants(new, old, evidence=evidence("c1", "c2"))
    assert merged.items["r"].passages == {"c1", "c2"}
    empty = Register(question="q", items={"r": item(status="resolved", passages={"fake"})})
    assert (
        apply_invariants(empty, Register(question="q"), evidence={}).items["r"].status == "active"
    )


@pytest.mark.parametrize(
    "reason,expected", [("", "active"), ("   ", "active"), ("Premise contradicted", "retired")]
)
def test_retirement_requires_reason(reason, expected):
    old = Register(question="q", items={"r": item(status="active")})
    new = Register(question="q", items={"r": item(status="retired", retire_reason=reason)})
    assert apply_invariants(new, old, evidence={}).items["r"].status == expected


def test_unrelated_edits_ignored_but_one_passage_can_close_multiple_needs():
    old = Register(question="q", items={"a": item(status="active"), "b": item(), "c": item()})
    new = Register(
        question="q",
        items={
            "a": item(status="resolved", passages={"c1"}, touched_passages={"c1"}),
            "b": item(status="resolved", passages={"c1"}, touched_passages={"c1"}),
            "c": item(text="unrelated rewrite", status="retired", retire_reason="guess"),
        },
    )
    merged = apply_invariants(new, old, evidence=evidence("c1"), fresh_ids={"c1"})
    assert merged.items["a"].status == merged.items["b"].status == "resolved"
    assert merged.items["c"].text == old.items["c"].text
    assert merged.items["c"].status == "active"


def test_only_confirmed_anchors_enter_query_and_dependencies_gate_scheduling():
    register = Register(
        question="Original goal",
        items={
            "a": item(status="active", passages={"c1"}, anchors=["Bridge", "Wrong"]),
            "b": item(status="active", depends_on=["a"]),
        },
    )
    merged = apply_invariants(register, Register(question="Original goal"), evidence=evidence("c1"))
    assert merged.items["a"].anchors == ["Bridge"]
    assert merged.items["b"].status == "pending"
    query = compose(merged.question, merged.items["a"])
    assert "Original goal" in query and "Who owns Acme?" in query and "Bridge" in query
    merged.items["a"].status = "resolved"
    schedule(merged)
    assert merged.items["b"].status == "active"
    assert "Bridge" in compose(merged.question, merged.search_target(merged.items["b"]))


@pytest.mark.parametrize("deps", [["missing"], ["r"]])
def test_invalid_dependency_graph_rejected(deps):
    register = Register(question="q", items={"r": item(depends_on=deps)})
    with pytest.raises(ValueError):
        apply_invariants(register, Register(question="q"), evidence={})


def test_retired_dependency_is_blocked_and_fair_scheduler_avoids_starvation():
    register = Register(
        question="q",
        items={
            "a": item(status="retired", retire_reason="contradicted"),
            "b": item(depends_on=["a"]),
        },
    )
    schedule(register)
    assert not register.ready_items() and not register.is_clear()
    register = Register(
        question="q", items={"a": item(req_id="a", status="active"), "b": item(req_id="b")}
    )
    schedule(register, attempts={"a": 1})
    assert register.items["b"].status == "active"
    assert diff(Register(question="q"), register)[0]["before"] == "new"


def test_rewriting_text_or_dropping_dependency_cannot_erase_a_need():
    old = Register(
        question="q",
        items={
            "a": item(req_id="a", status="active"),
            "b": item(text="What is the owner's festival date?", depends_on=["a"]),
        },
    )
    proposed = Register(
        question="q",
        items={
            "b": item(
                text="Who owns Acme?", depends_on=[], passages={"c1"}, touched_passages={"c1"}
            )
        },
    )
    merged = apply_invariants(proposed, old, evidence=evidence("c1"), fresh_ids={"c1"})
    assert merged.items["b"].text == old.items["b"].text
    assert merged.items["b"].depends_on == ["a"] and merged.items["b"].status == "pending"


def test_keyword_cache_normalizes_order_and_semantic_preserves_it():
    assert query_key("fulltext_search", {"query": " Bridge  Acme "}) == query_key(
        "fulltext_search", {"query": "acme bridge"}
    )
    assert query_key("vector_search", {"query": "Bridge Acme"}) != query_key(
        "vector_search", {"query": "Acme Bridge"}
    )
    assert query_key("vector_search", {"query": " Q ", "top_k": 5}) != query_key(
        "vector_search", {"query": "q", "top_k": 10}
    )


def test_config_and_state_are_validated():
    with pytest.raises(ValidationError):
        RequirementRegisterConfig(convergence_ratio=0)
    with pytest.raises(ValidationError):
        RequirementRegisterConfig(unknown=True)
    with pytest.raises(ValidationError):
        item(status="done")
