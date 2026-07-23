"""Relation scoring helpers for graph beam retrieval."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from agentic_graphrag.stores.interfaces import EntityRecord, RelationRecord

RelationEmbedSim = Callable[[str, str | None], float | None]

# Lexical relation cues; optional embed blend via blend_relation_score.
_RELATION_CUES: dict[str, tuple[str, ...]] = {
    "CEO_OF": ("ceo", "chief executive", "leads", "leader", "headed by"),
    "WORKS_AT": ("work", "works", "worked", "employ", "job", "role", "title"),
    "WORKED_AT": ("previously work", "worked at", "former", "prior", "previously"),
    "PARENT_OF": ("parent", "owns", "own", "holding", "subsidiary of", "parent company"),
    "SUBSIDIARY_OF": ("subsidiary", "owned by", "child company", "unit of"),
    "PRODUCES": ("produce", "product", "manufactur", "makes", "offers"),
    "COMPETES_WITH": ("compet", "rival", "versus", "vs"),
    "SUPPLIES": ("suppl", "vendor", "logistics"),
    "SUPPLIES_FOR": ("suppl", "component", "parts for"),
    "PARTICIPATED_IN": ("participat", "event", "partnership", "deal", "summit"),
    "ACQUIRED": ("acquir", "bought", "merger", "m&a"),
    "LOCATED_IN": ("located", "based in", "headquarter", "hq"),
}

_EMBED_RERANK_WEIGHT = 0.35


def blend_relation_score(
    lexical: float,
    embed_sim: float | None,
    *,
    embed_weight: float = _EMBED_RERANK_WEIGHT,
) -> float:
    """Blend lexical cue with optional embed sim in [0,1]; None embed keeps lexical."""
    lex = max(0.0, min(1.0, lexical))
    if embed_sim is None:
        return lex
    w = max(0.0, min(1.0, embed_weight))
    emb = max(0.0, min(1.0, float(embed_sim)))
    return (1.0 - w) * lex + w * emb


def relation_relevance(relation_type: str, sub_question: str | None) -> float:
    """Score in [0, 1] how well a relation type matches the sub-question text."""
    if not sub_question:
        return 0.5
    q = sub_question.lower()
    cues = _RELATION_CUES.get(relation_type.upper(), ())
    if not cues:
        # Unknown relation: mild default so it is not totally pruned
        return 0.25
    hits = sum(1 for c in cues if c in q)
    if hits == 0:
        return 0.08
    return min(1.0, 0.45 + 0.2 * hits)


def infer_relation_types(
    sub_question: str | None,
    *,
    available: Iterable[str] | None = None,
    min_score: float = 0.2,
) -> list[str] | None:
    """Return preferred relation types for the sub-question, or None if unfiltered.

    None means "no strong cue" → caller may still score edges but not hard-filter.
    """
    if not sub_question or not sub_question.strip():
        return None
    avail = {a.upper() for a in available} if available is not None else None
    scored = _score_relation_cues(sub_question, avail, min_score)
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [r for _, r in scored]


def _score_relation_cues(
    sub_question: str,
    avail: set[str] | None,
    min_score: float,
) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for rel in _RELATION_CUES:
        if avail is not None and rel not in avail:
            continue
        s = relation_relevance(rel, sub_question)
        if s >= min_score:
            scored.append((s, rel))
    return scored


def edge_score(
    rel: RelationRecord,
    sub_question: str | None,
    *,
    embed_sim: float | None = None,
) -> float:
    conf = float(rel.confidence) if rel.confidence is not None else 1.0
    conf = max(0.0, min(1.0, conf))
    rel_score = blend_relation_score(relation_relevance(rel.type, sub_question), embed_sim)
    return conf * rel_score


def path_signature(nodes: list[EntityRecord], rels: list[RelationRecord]) -> str:
    parts: list[str] = []
    for i, n in enumerate(nodes):
        parts.append(n.name.lower())
        if i < len(rels):
            parts.append(rels[i].type.upper())
    return "|".join(parts)


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())
