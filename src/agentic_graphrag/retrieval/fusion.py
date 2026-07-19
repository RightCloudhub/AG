"""Fusion ranking: RRF + re-ranker interface (FR-RT-04 / P3-PERF-03).

RRF is the default production fusion. Learning re-rankers plug in via
:class:`Reranker` without changing the Executor call sites.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentic_graphrag.retrieval.contracts import (
    Candidate,
    CandidateSource,
    concat_candidates,
    rrf_fuse,
)

__all__ = [
    "Reranker",
    "IdentityReranker",
    "fuse_candidates",
    "concat_candidates",
    "rrf_fuse",
]


@runtime_checkable
class Reranker(Protocol):
    """Learning re-ranker hook (P5-CAP-04). Identity is the V1 default."""

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]: ...


class IdentityReranker:
    """No-op re-ranker — preserves RRF order."""

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        del query
        return list(candidates)


def fuse_candidates(
    *ranked_lists: list[Candidate],
    query: str = "",
    method: str = "rrf",
    k: int = 60,
    limit: int | None = None,
    reranker: Reranker | None = None,
) -> list[Candidate]:
    """Fuse multi-channel ranked lists then optional re-rank.

    ``method``:
    - ``rrf`` — Reciprocal Rank Fusion (default, P3)
    - ``concat`` — POC simple concat (legacy)
    """
    lists = [lst for lst in ranked_lists if lst]
    if not lists:
        return []

    method_l = (method or "rrf").lower().strip()
    if method_l == "concat":
        fused = concat_candidates(*lists)
    else:
        fused = rrf_fuse(*lists, k=k, limit=None)

    if reranker is not None:
        fused = reranker.rerank(query, fused)
    if limit is not None:
        fused = fused[:limit]

    # Ensure fusion channel tag when multi-list
    if len(lists) > 1 and method_l != "concat":
        out: list[Candidate] = []
        for c in fused:
            if c.source != CandidateSource.FUSION:
                out.append(
                    Candidate(
                        id=c.id,
                        source=CandidateSource.FUSION,
                        content=c.content,
                        score=c.score,
                        structured={
                            **c.structured,
                            "origin_source": c.source.value,
                        },
                        citations=list(c.citations),
                        metadata=dict(c.metadata),
                    )
                )
            else:
                out.append(c)
        return out
    return fused
