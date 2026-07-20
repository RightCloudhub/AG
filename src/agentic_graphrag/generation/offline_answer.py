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
    # Use full evidence for focused multi-hop rules (parent→CEO needs both edges).
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

    # Factoid CEO / subsidiary: honest no_answer instead of dumping unrelated edges.
    if _should_honest_no_answer(chain.question, edges, conclusions):
        chain.honest_fallback("no matching relation for the asked entity")
        chain.metadata["offline_answerer"] = "honest_no_match"
        return chain

    # Extractive dump: prefer candidates that mention question tokens (noise cut).
    dump_src = _prefer_question_aligned(chain.question, preferred) or preferred
    dump_texts = [c.content for c in dump_src]
    facts = dump_texts[:6]
    if conclusions:
        facts = [conclusions] + facts
    chain.answer = " | ".join(facts)
    chain.status = QueryStatus.PARTIAL
    chain.claims = bind_claims_to_evidence(
        [Claim(text=c.content[:200], evidence_ids=[c.id]) for c in dump_src[:5]],
        dump_src,
    )
    chain.metadata["offline_answerer"] = "extractive"
    return chain


def _prefer_question_aligned(
    question: str, candidates: list[Candidate]
) -> list[Candidate]:
    ql = (question or "").lower()
    if not ql or not candidates:
        return candidates
    aligned = [c for c in candidates if _content_aligns(ql, c.content)]
    return aligned if aligned else candidates


def _content_aligns(ql: str, content: str) -> bool:
    cl = (content or "").lower()
    if "-[" not in cl:
        return any(tok in cl for tok in ql.split() if len(tok) > 3)
    try:
        left, rest = cl.split("-[", 1)
        right = rest.split("]->", 1)[1]
        head = left.strip()
        tail = right.split("(")[0].strip()
    except (IndexError, ValueError):
        return False
    return (head and head in ql) or (tail and tail in ql)


def _should_honest_no_answer(
    question: str,
    edges: list[tuple[str, str, str]],
    conclusions: str,
) -> bool:
    """True for closed factoid questions that have no supporting edge."""
    q = (question or "").lower()
    if conclusions and conclusions.strip().lower() in {"yes", "no"}:
        return False
    if "ceo" in q:
        return not _has_ceo_for_question(q, edges)
    if _is_yes_no_subsidiary(q):
        return not _has_subsidiary_evidence(q, edges)
    return False


def _has_ceo_for_question(q: str, edges: list[tuple[str, str, str]]) -> bool:
    # Multi-hop "CEO of parent of X": CEO of parent company also counts.
    parent_names: set[str] = set()
    for h, r, t in edges:
        if r == "PARENT_OF" and t.lower() in q:
            parent_names.add(h.lower())
        if r == "SUBSIDIARY_OF" and h.lower() in q:
            parent_names.add(t.lower())
    for h, r, t in edges:
        if r != "CEO_OF":
            continue
        tl = t.lower()
        if tl in q or tl in parent_names:
            return True
        if any(p in tl or tl in p for p in parent_names):
            return True
    return False


def _is_yes_no_subsidiary(q: str) -> bool:
    if "subsidiary" not in q and "parent" not in q:
        return False
    return q.strip().startswith("is ") or " is " in q


def _has_subsidiary_evidence(q: str, edges: list[tuple[str, str, str]]) -> bool:
    for h, r, t in edges:
        if r == "PARENT_OF" and h.lower() in q and t.lower() in q:
            return True
        if r == "SUBSIDIARY_OF" and h.lower() in q and t.lower() in q:
            return True
        if r in {"PARENT_OF", "SUBSIDIARY_OF"}:
            if any(tok in h.lower() for tok in q.split() if len(tok) > 4) and any(
                tok in t.lower() for tok in q.split() if len(tok) > 4
            ):
                return True
    return False
