"""Offline extractive answer heuristics (split from answer.py for size).

Used when LLM generation is disabled. Citation binding still goes through
``generation.citations`` so AC-7 rules apply uniformly.

Edge parsing: :mod:`agentic_graphrag.generation.offline_edges`.
Focused multi-hop heuristics: :mod:`agentic_graphrag.generation.offline_heuristics`.
"""

from __future__ import annotations

from agentic_graphrag.generation.citations import bind_claims_to_evidence
from agentic_graphrag.generation.offline_edges import parse_edges
from agentic_graphrag.generation.offline_heuristics import focused_extract
from agentic_graphrag.generation.trace import Claim, QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.contracts import Candidate


def offline_answer(
    chain: ReasoningChain,
    evidence: list[Candidate],
    conclusions: str,
) -> ReasoningChain:
    """Deterministic multi-hop extractive answer from graph/text evidence."""
    graph = [c for c in evidence if c.is_graph()]
    preferred = graph if graph else evidence
    texts = [c.content for c in preferred]
    edges = parse_edges(texts)
    focused = focused_extract(chain.question, edges, texts)

    if focused:
        chain.answer = focused
        chain.status = QueryStatus.ANSWERED if graph else QueryStatus.PARTIAL
        chain.claims = bind_claims_to_evidence(
            [Claim(text=focused, evidence_ids=[c.id for c in preferred[:5]])],
            preferred,
            fallback_text=focused,
        )
        chain.metadata["offline_answerer"] = "focused"
        return chain

    facts = texts[:6]
    if conclusions:
        facts = [conclusions] + facts
    chain.answer = " | ".join(facts)
    chain.status = QueryStatus.PARTIAL
    chain.claims = bind_claims_to_evidence(
        [Claim(text=c.content[:200], evidence_ids=[c.id]) for c in preferred[:5]],
        preferred,
    )
    chain.metadata["offline_answerer"] = "extractive"
    return chain
