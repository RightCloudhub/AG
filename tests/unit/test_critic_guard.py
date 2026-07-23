"""Tests for incomplete multi-hop critic guard."""

from __future__ import annotations

from agentic_graphrag.agent.critic import CriticAction, CriticResult, CriticScope
from agentic_graphrag.agent.critic_guard import force_incomplete_multihop
from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation


def _cand(text: str) -> Candidate:
    return Candidate(
        id="c1",
        source=CandidateSource.GRAPH_NEIGHBOR,
        content=text,
        score=1.0,
        citations=[Citation(span=text)],
    )


def test_forces_ceo_hop_when_only_competitor_found():
    ctx = CritiqueContext(
        question="Who is the CEO of the competitor of the producer of Polaris Stack?",
        sub_question="Who is a competitor of Polaris Cloud?",
        evidence=[
            _cand("Polaris Cloud -[COMPETES_WITH]-> NovaTech Industries (ORG)"),
        ],
        explored_paths=["Polaris Stack -> Polaris Cloud"],
        hop=2,
        max_hops=5,
    )
    result = CriticResult(
        action=CriticAction.SUFFICIENT,
        scope=CriticScope.GLOBAL,
        global_answered=True,
        sub_answered=True,
        partial_answer="NovaTech Industries is a competitor of Polaris Cloud",
    )
    out = force_incomplete_multihop(ctx, result)
    assert out.action == CriticAction.NEXT_HOP
    assert out.new_sub_question
    assert (
        "CEO" in (out.new_sub_question or "").upper()
        or "ceo" in (out.new_sub_question or "").lower()
    )


def test_no_force_when_ceo_edge_present():
    ctx = CritiqueContext(
        question="Who is the CEO of the competitor of the producer of X?",
        sub_question="Who is the CEO of NovaTech?",
        evidence=[_cand("Marcus Chen -[CEO_OF]-> NovaTech Industries (PERSON)")],
        hop=3,
        max_hops=5,
    )
    result = CriticResult(
        action=CriticAction.SUFFICIENT,
        scope=CriticScope.GLOBAL,
        global_answered=True,
    )
    out = force_incomplete_multihop(ctx, result)
    assert out.action == CriticAction.SUFFICIENT
