"""Direction-checked frame resolution (relation_frames) and critic integration."""

from __future__ import annotations

from agentic_graphrag.agent.critic_offline import extract_entity_conclusion
from agentic_graphrag.agent.frame_resolution import (
    frame_anchors,
    frame_for_subquestion,
    match_edge,
    resolve_frame_answers,
)
from agentic_graphrag.agent.relation_frames import frame
from agentic_graphrag.core.evidence import Candidate, CandidateSource


def _edge(head: str, rel: str, tail: str, seed: str = "") -> Candidate:
    return Candidate(
        id=f"nbr:{seed or head}:{rel}:{tail}",
        source=CandidateSource.GRAPH_NEIGHBOR,
        content=f"{head} -[{rel}]-> {tail}",
        score=1.0,
        structured={"query_entity": seed or head, "relation": rel, "head": head, "tail": tail},
    )


def _text(content: str) -> Candidate:
    return Candidate(id="t1", source=CandidateSource.FULLTEXT_CHUNK, content=content, score=1.0)


def test_frame_for_subquestion_canonical_shapes_only() -> None:
    a = ["Acme Corp", "Beta Inc"]
    assert frame_for_subquestion("What is the parent company of Acme Corp?", a) == frame("parent")
    assert frame_for_subquestion("Who is the CEO of Acme Corp and Beta Inc?", a) == frame("ceo")
    assert frame_for_subquestion("Which company produces Acme Corp?", a) == frame("producer")
    assert frame_for_subquestion("Who is the chief executive of Acme Corp?", a) == frame("ceo")
    assert frame_for_subquestion("Where is Acme Corp headquartered?", a) == frame("hq")
    assert frame_for_subquestion("Who previously worked at Acme Corp?", a) == frame("alumni")
    # Free-form / direction-ambiguous questions get no frame.
    q = "Which CEO previously worked at Acme Corp and now leads Beta Inc?"
    assert frame_for_subquestion(q, a) is None
    assert frame_for_subquestion("What is the color of Acme Corp?", a) is None


def test_parent_requires_anchor_on_tail_of_parent_of() -> None:
    seed = "Polaris Cloud"
    hits = [
        _edge("Polaris Cloud", "PARENT_OF", "BluePeak Cloud", seed),  # child, wrong side
        _edge("Polaris Cloud", "PARENT_OF", "Lattice AI", seed),
        _edge("Orion Systems", "PARENT_OF", "Polaris Cloud", seed),  # the real parent
    ]
    assert resolve_frame_answers("What is the parent company of Polaris Cloud?", hits) == [
        "Orion Systems"
    ]


def test_subsidiary_of_edge_answers_parent_from_tail() -> None:
    hits = [_edge("Willow Telecom", "SUBSIDIARY_OF", "Lumen Networks")]
    ans = resolve_frame_answers("What is the parent company of Willow Telecom?", hits)
    assert ans == ["Lumen Networks"]


def test_edges_not_touching_anchor_are_ignored() -> None:
    seed = "NovaTech Industries"
    hits = [
        _edge("Apex Holdings", "PARENT_OF", "BrightLink Logistics", seed),
        _edge("Marcus Chen", "CEO_OF", "NovaTech Industries", seed),
    ]
    assert resolve_frame_answers("Who is the CEO of NovaTech Industries?", hits) == ["Marcus Chen"]
    assert resolve_frame_answers("What is the parent company of NovaTech Industries?", hits) == []


def test_multi_frame_keeps_all_and_single_keeps_first() -> None:
    seed = "Cedar Semiconductor"
    hits = [
        _edge("Cedar Semiconductor", "COMPETES_WITH", "SiliconForge", seed),
        _edge("Vertex Chips", "COMPETES_WITH", "Cedar Semiconductor", seed),
    ]
    ans = resolve_frame_answers("Which company competes with Cedar Semiconductor?", hits)
    assert ans == ["SiliconForge", "Vertex Chips"]
    ceo_hits = [
        _edge("Ann Old", "CEO_OF", "Acme", "Acme"),
        _edge("Bob New", "CEO_OF", "Acme", "Acme"),
    ]
    assert resolve_frame_answers("Who is the CEO of Acme?", ceo_hits) == ["Ann Old"]


def test_fan_out_anchors_one_answer_each() -> None:
    hits = [
        _edge("Hans Richter", "CEO_OF", "Ember Foundry", "Ember Foundry"),
        _edge("Hiro Tanaka", "CEO_OF", "SiliconForge", "SiliconForge"),
    ]
    q = "Who is the CEO of Ember Foundry and SiliconForge?"
    assert frame_anchors(q, hits) == ["Ember Foundry", "SiliconForge"]
    assert resolve_frame_answers(q, hits) == ["Hans Richter", "Hiro Tanaka"]


def test_nested_names_and_non_graph_evidence() -> None:
    hits = [_edge("Helix Compute", "PRODUCES", "HelixCore Server"), _text("Helix")]
    assert frame_anchors("Which company produces HelixCore Server?", hits) == ["HelixCore Server"]
    assert match_edge(frame("producer"), "", hits[0]) is None
    assert match_edge(frame("producer"), "x", _text("no structure")) is None
    assert resolve_frame_answers("Which company produces HelixCore Server?", hits) == [
        "Helix Compute"
    ]


def test_unknown_frame_or_anchor_returns_empty() -> None:
    hits = [_edge("A Corp", "PARTNER_OF", "B Corp")]
    assert resolve_frame_answers("Tell me about A Corp", hits) is None  # no frame shape
    assert resolve_frame_answers("Who is the CEO of Zed Inc?", hits) is None  # no anchor
    assert resolve_frame_answers("Who is the CEO of A Corp?", hits) == []  # frame, no edge
    assert extract_entity_conclusion("Who is the CEO of A Corp?", hits) is None


def test_critic_conclusion_uses_direction_checked_frame() -> None:
    seed = "Polaris Cloud"
    hits = [
        _edge("Polaris Cloud", "PARENT_OF", "BluePeak Cloud", seed),
        _edge("Orion Systems", "PARENT_OF", "Polaris Cloud", seed),
    ]
    got = extract_entity_conclusion("What is the parent company of Polaris Cloud?", hits)
    assert got == "Orion Systems"
