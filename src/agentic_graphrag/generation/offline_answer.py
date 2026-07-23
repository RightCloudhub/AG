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
    hop_answer = _prefer_hop_conclusion(chain.question, conclusions)
    if hop_answer:
        return _apply_focused(chain, hop_answer, preferred)
    focused = focused_extract(chain.question, edges, texts)
    if focused:
        return _apply_focused(chain, focused, preferred)
    if _should_honest_no_answer(chain.question, edges, conclusions):
        chain.honest_fallback("no matching relation for the asked entity")
        chain.metadata["offline_answerer"] = "honest_no_match"
        return chain
    return _apply_extractive(chain, preferred, conclusions)


def _prefer_hop_conclusion(question: str, conclusions: str) -> str | None:
    """Use the last multi-hop conclusion when the question asks for a person/CEO."""
    ql = (question or "").lower()
    if "ceo" not in ql and "who is" not in ql:
        return None
    parts = [p.strip() for p in (conclusions or "").split(";") if p.strip()]
    if not parts:
        return None
    last = parts[-1]
    # Skip pure company/product fragments that are intermediate hops.
    if any(k in last.lower() for k in ("fpga", "server", "chip", "product")):
        return None
    return last


def _apply_focused(
    chain: ReasoningChain,
    focused: str,
    preferred: list[Candidate],
) -> ReasoningChain:
    has_graph = any(c.is_graph() for c in preferred)
    chain.answer = focused
    chain.status = QueryStatus.ANSWERED if has_graph else QueryStatus.PARTIAL
    chain.claims = bind_claims_to_evidence(
        [Claim(text=focused, evidence_ids=[c.id for c in preferred[:5]])],
        preferred,
        fallback_text=focused,
    )
    chain.metadata["offline_answerer"] = "focused"
    return chain


def _apply_extractive(
    chain: ReasoningChain,
    preferred: list[Candidate],
    conclusions: str,
) -> ReasoningChain:
    dump_src = _prefer_question_aligned(chain.question, preferred) or preferred
    facts = [c.content for c in dump_src][:6]
    if conclusions:
        facts = [conclusions, *facts]
    chain.answer = " | ".join(facts)
    chain.status = QueryStatus.PARTIAL
    chain.claims = bind_claims_to_evidence(
        [Claim(text=c.content[:200], evidence_ids=[c.id]) for c in dump_src[:5]],
        dump_src,
    )
    chain.metadata["offline_answerer"] = "extractive"
    return chain


def _prefer_question_aligned(question: str, candidates: list[Candidate]) -> list[Candidate]:
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


def _parent_names_for_question(q: str, edges: list[tuple[str, str, str]]) -> set[str]:
    parents: set[str] = set()
    for head, rel, tail in edges:
        if rel == "PARENT_OF" and tail.lower() in q:
            parents.add(head.lower())
        if rel == "SUBSIDIARY_OF" and head.lower() in q:
            parents.add(tail.lower())
    return parents


def _has_ceo_for_question(q: str, edges: list[tuple[str, str, str]]) -> bool:
    # Multi-hop "CEO of parent of X": CEO of parent company also counts.
    parent_names = _parent_names_for_question(q, edges)
    for _head, rel, tail in edges:
        if rel != "CEO_OF":
            continue
        if _ceo_edge_matches(tail.lower(), q, parent_names):
            return True
    return False


def _ceo_edge_matches(tail_l: str, q: str, parent_names: set[str]) -> bool:
    if tail_l in q or tail_l in parent_names:
        return True
    return any(p in tail_l or tail_l in p for p in parent_names)


def _is_yes_no_subsidiary(q: str) -> bool:
    if "subsidiary" not in q and "parent" not in q:
        return False
    return q.strip().startswith("is ") or " is " in q


def _tokens_gt4(q: str) -> list[str]:
    return [tok for tok in q.split() if len(tok) > 4]


def _edge_mentions_q_tokens(name: str, tokens: list[str]) -> bool:
    low = name.lower()
    return any(tok in low for tok in tokens)


def _has_subsidiary_evidence(q: str, edges: list[tuple[str, str, str]]) -> bool:
    tokens = _tokens_gt4(q)
    for edge in edges:
        if _direct_subsidiary_pair(q, edge):
            return True
        head, rel, tail = edge
        if rel not in {"PARENT_OF", "SUBSIDIARY_OF"} or not tokens:
            continue
        if _edge_mentions_q_tokens(head, tokens) and _edge_mentions_q_tokens(tail, tokens):
            return True
    return False


def _direct_subsidiary_pair(q: str, edge: tuple[str, str, str]) -> bool:
    head, rel, tail = edge
    hl, tl = head.lower(), tail.lower()
    if rel == "PARENT_OF" and hl in q and tl in q:
        return True
    return rel == "SUBSIDIARY_OF" and hl in q and tl in q
