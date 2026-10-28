"""Compositional relation-chain planning (planner_chain)."""

from __future__ import annotations

from agentic_graphrag.agent.planner import plan_offline
from agentic_graphrag.agent.planner_chain import normalize_chain, parse_chain

KNOWN = ["Northwind Insight Suite", "NovaTech Industries", "Priya Nair", "Marcus Chen", "Zed"]


def _texts(question: str) -> list[str]:
    return [sq.text for sq in plan_offline(question, known_entities=KNOWN)]


def test_three_hop_chain_innermost_first() -> None:
    q = "Who is the CEO of the parent company of the producer of Northwind Insight Suite?"
    assert _texts(q) == [
        "Which company produces Northwind Insight Suite?",
        "What is the parent company of {from:sq1}?",
        "Who is the CEO of {from:sq2}?",
    ]
    sqs = plan_offline(q, known_entities=KNOWN)
    assert sqs[2].depends_on == ["sq2"] and sqs[2].is_placeholder


def test_lead_verb_and_relative_clause_rewrites() -> None:
    assert _texts("Which company competes with the producer of Northwind Insight Suite?") == [
        "Which company produces Northwind Insight Suite?",
        "Which company competes with {from:sq1}?",
    ]
    assert _texts("Who is the CEO of the company that competes with NovaTech Industries?") == [
        "Which company competes with NovaTech Industries?",
        "Who is the CEO of {from:sq1}?",
    ]
    assert normalize_chain("Who founded the company where Marcus Chen previously worked?") == (
        "who is the founder of the previous employer of marcus chen"
    )


def test_outer_verb_frames() -> None:
    q = (
        "Which companies did the CEO of the parent company of NovaTech Industries "
        "previously work at?"
    )
    assert _texts(q)[-1] == "Which companies did {from:sq2} previously work at?"
    assert _texts("Where is the parent company of NovaTech Industries headquartered?") == [
        "What is the parent company of NovaTech Industries?",
        "Where is {from:sq1} headquartered?",
    ]
    q2 = "Which event did the investor that Priya Nair previously worked at participate in?"
    assert _texts(q2) == [
        "Which companies did Priya Nair previously work at?",
        "Which event did {from:sq1} participate in?",
    ]


def test_partial_or_single_hop_parses_fall_through() -> None:
    # Single frame: left to the specific matchers.
    assert parse_chain("Who is the CEO of NovaTech Industries?", ["NovaTech Industries"]) is None
    # Unparsed qualifier ("power modules") must not be silently dropped.
    q = "Who founded the company that supplies power modules for NovaTech Industries?"
    assert parse_chain(q, ["NovaTech Industries"]) is None
    assert parse_chain("Who is the CEO of the parent company of Unknown Co?", ["Zed"]) is None
    assert parse_chain("Who is the CEO of the parent company of X?", []) is None


def test_constraint_intersection_plan_and_answer() -> None:
    from agentic_graphrag.agent.planner_constraints import (
        intersect_conclusions,
        parse_constraints,
    )

    known = ["Meridian Capital", "Helix Compute"]
    q = "Which CEO previously worked at Meridian Capital and now leads Helix Compute?"
    assert [sq.text for sq in plan_offline(q, known_entities=known)] == [
        "Who previously worked at Meridian Capital?",
        "Who is the CEO of Helix Compute?",
    ]
    got = intersect_conclusions(q, ["Elena Varga and Priya Nair", "Priya Nair"], known)
    assert got == "Priya Nair"
    assert intersect_conclusions(q, ["Elena Varga", "Priya Nair"], known) is None
    assert intersect_conclusions(q, ["Priya Nair"], known) is None
    assert parse_constraints("Which CEO leads Helix Compute?", known) is None
    assert parse_constraints("Which CEO leads Helix Compute and likes tea?", known) is None
    assert parse_constraints("Which CEO leads Unknown Co and owns Helix Compute?", known) is None


def test_possessive_and_lead_verb_synonyms() -> None:
    known = ["Polaris Cloud"]
    assert [
        sq.text
        for sq in plan_offline(
            "Who is the CEO of Polaris Cloud's parent company?", known_entities=known
        )
    ] == [
        "What is the parent company of Polaris Cloud?",
        "Who is the CEO of {from:sq1}?",
    ]
    assert (
        len(plan_offline("Who runs the parent company of Polaris Cloud?", known_entities=known))
        == 2
    )
