"""Production relation-type embedder for graph beam scoring (P2-RT-01).

Builds a :class:`~agentic_graphrag.retrieval.graph_beam.RelationEmbedSim`
callback from an LLM embedding client. Lexical scoring remains the default
when this scorer is ``None`` or returns ``None``.
"""

from __future__ import annotations

import math
from typing import Protocol

from agentic_graphrag.retrieval.graph_beam import RelationEmbedSim


class _EmbedClient(Protocol):
    def embed(self, text: str) -> list[float]: ...


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity mapped to [0, 1] (negative → 0)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def make_relation_embed_sim(
    llm: _EmbedClient,
    *,
    cache: dict[str, list[float]] | None = None,
) -> RelationEmbedSim:
    """Return a scorer: relation type + sub-question → cosine sim in [0,1]."""
    store = cache if cache is not None else {}

    def _embed_cached(text: str) -> list[float] | None:
        key = text.strip().lower()
        if not key:
            return None
        if key in store:
            return store[key]
        try:
            vec = llm.embed(text)
        except Exception:  # noqa: BLE001 — never break retrieval
            return None
        store[key] = list(vec)
        return store[key]

    def score(relation_type: str, sub_question: str | None) -> float | None:
        q = (sub_question or "").strip()
        rel = (relation_type or "").strip()
        if not q or not rel:
            return None
        # Humanize RELATION_TYPE for better semantic match with natural language.
        rel_text = rel.replace("_", " ").lower()
        va = _embed_cached(rel_text)
        vb = _embed_cached(q)
        if va is None or vb is None:
            return None
        return cosine_sim(va, vb)

    return score
