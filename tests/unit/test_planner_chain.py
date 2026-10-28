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
    # The qualifier ("power modules") is kept on its hop, never silently dropped.
    q = "Who founded the company that supplies power modules for NovaTech Industries?"
    parsed = parse_chain(q, ["NovaTech Industries"])
    assert parsed is not None and parsed.qualifiers == ("power modules", "")
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
    single = parse_constraints("Which CEO leads Helix Compute?", known)
    assert single is not None and len(single) == 1 and single[0].frame.key == "ceo"
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


def test_qualified_supplier_chain_and_acquisition_outer() -> None:
    from agentic_graphrag.agent.planner_chain import chain_subquestions

    q = "Which company did the chip supplier of QuantumEdge Server acquire?"
    parsed = parse_chain(q, ["QuantumEdge Server"])
    assert parsed is not None and [f.key for f in parsed.frames] == ["supplier", "acquisition"]
    assert [sq.text for sq in chain_subquestions(parsed)] == [
        "Which companies supply chip for QuantumEdge Server?",
        "Which company did {from:sq1} acquire?",
    ]
    # A qualifier that names an entity is not a qualifier.
    assert parse_chain("Who founded the Acme supplier of Beta?", ["Acme", "Beta"]) is None


def test_constraint_clause_with_chain_object_and_filler() -> None:
    from agentic_graphrag.agent.planner_constraints import (
        constraint_subquestions,
        intersect_conclusions,
        parse_constraints,
    )

    known = ["HelixCore Server", "QuantumEdge Server"]
    q = (
        "Which company supplies HelixCore Server and was acquired by the chip supplier "
        "of QuantumEdge Server?"
    )
    cons = parse_constraints(q, known)
    assert cons is not None
    plan = constraint_subquestions(cons)
    assert [sq.text for sq in plan] == [
        "Which companies supply HelixCore Server?",
        "Which companies supply chip for QuantumEdge Server?",
        "Which company did {from:sq2} acquire?",
    ]
    concl = ["Lumen Photonics and Acme", "SiliconForge", "Lumen Photonics"]
    assert intersect_conclusions(q, concl, known) == "Lumen Photonics"
    alumni = parse_constraints(
        "Which executives in this corpus previously worked at Orion?", ["Orion"]
    )
    assert alumni is not None and alumni[0].frame.key == "alumni"


def test_compound_follow_up_plans_from_first_answer() -> None:
    from agentic_graphrag.agent.planner_compound import compound_answer

    known = ["NovaTech Industries"]
    q = "What is the parent company of NovaTech Industries, and who leads it?"
    plan = plan_offline(q, known_entities=known)
    assert [sq.text for sq in plan] == [
        "What is the parent company of NovaTech Industries?",
        "Who is the CEO of {from:sq1}?",
    ]
    assert plan[1].depends_on == ["sq1"]
    assert compound_answer(q, ["Apex Holdings", "Elena Varga"], known) == (
        "Apex Holdings; Elena Varga"
    )
