"""Second-stage re-rankers behind :class:`~agentic_graphrag.retrieval.fusion.Reranker` (O-5).

Two-stage retrieval: each channel recalls ``candidate_pool`` hits, RRF fuses
them, then a re-ranker reorders the fused list by *content* before the top-N
cut. RRF only sees ranks, so near-duplicate "similar but off-topic" chunks that
two channels agree on float to the top; a content-aware stage fixes that.

* :class:`LexicalReranker` — offline, deterministic (query-token coverage
  blended with the RRF prior). Useful as a baseline and in tests.
* :class:`HttpCrossEncoderReranker` — a cross-encoder served over HTTP
  (TEI / Infinity ``POST /rerank``: ``{"query", "texts"}`` →
  ``[{"index", "score"}]``), e.g. ``bge-reranker-v2-m3``. Any error or timeout
  falls back to the input order and tags ``metadata.rerank_fallback``.

Graph hits are already linearised text (``A -[REL]-> B``), so they are scored
like chunks.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agentic_graphrag.core.evidence import Candidate

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_STOP = frozenset(
    "a an the of to in on at for by with is are was were be who what which whom whose "
    "how when where did does do and or that this these those company companies".split()
)
_PRIOR_WEIGHT = 0.5


def _terms(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if t.lower() not in _STOP}


def _with_meta(c: Candidate, **meta: Any) -> Candidate:
    return c.model_copy(update={"metadata": {**c.metadata, **meta}})


class LexicalReranker:
    """Query-term coverage + RRF prior; ties keep the fused order."""

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        q = _terms(query)
        if not q or len(candidates) < 2:
            return list(candidates)
        scored = []
        for rank, c in enumerate(candidates):
            coverage = len(q & _terms(c.content)) / len(q)
            prior = _PRIOR_WEIGHT / (1 + rank)
            scored.append((coverage + prior, rank, c))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [_with_meta(c, rerank_score=round(s, 6), rerank="lexical") for s, _r, c in scored]


class HttpCrossEncoderReranker:
    """Cross-encoder over HTTP; never raises into retrieval."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 2.0, max_chars: int = 2000):
        if not endpoint:
            raise ValueError("HttpCrossEncoderReranker needs an endpoint")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.max_chars = max_chars
        self.fallbacks = 0

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        if len(candidates) < 2:
            return list(candidates)
        try:
            scores = self._score(query, [c.content[: self.max_chars] for c in candidates])
        except Exception as exc:  # noqa: BLE001 — degrade to fused order
            self.fallbacks += 1
            logger.warning("reranker fallback (%s): %s", type(exc).__name__, exc)
            return [_with_meta(c, rerank_fallback=type(exc).__name__) for c in candidates]
        order = sorted(range(len(candidates)), key=lambda i: (-scores.get(i, float("-inf")), i))
        return [
            _with_meta(candidates[i], rerank_score=scores.get(i), rerank="cross_encoder")
            for i in order
        ]

    def _score(self, query: str, texts: list[str]) -> dict[int, float]:
        import httpx

        resp = httpx.post(
            self.endpoint,
            json={"query": query, "texts": texts, "truncate": True},
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        rows = resp.json()
        if isinstance(rows, dict):  # some servers wrap: {"results": [...]}
            rows = rows.get("results") or []
        return {int(r["index"]): float(r.get("score", r.get("relevance_score", 0.0))) for r in rows}


def build_reranker(cfg: Any) -> LexicalReranker | HttpCrossEncoderReranker | None:
    """``cfg`` is :class:`~agentic_graphrag.config.RerankConfig` (kind none|lexical|http)."""
    kind = str(getattr(cfg, "kind", "none") or "none").lower()
    if kind == "none":
        return None
    if kind == "lexical":
        return LexicalReranker()
    if kind == "http":
        return HttpCrossEncoderReranker(
            str(getattr(cfg, "endpoint", "")),
            timeout_seconds=float(getattr(cfg, "timeout_seconds", 2.0)),
        )
    raise ValueError(f"unknown reranker kind {kind!r} (expected none|lexical|http)")
