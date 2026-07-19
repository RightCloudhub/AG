"""Unified retrieval candidate contract for Agent/Executor consumption."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CandidateSource(StrEnum):
    VECTOR = "vector"
    GRAPH = "graph"
    FULLTEXT = "fulltext"
    FUSION = "fusion"


class Citation(BaseModel):
    doc_id: str | None = None
    chunk_id: str | None = None
    entity_id: str | None = None
    relation_id: str | None = None
    span: str | None = None


class Candidate(BaseModel):
    id: str
    source: CandidateSource
    content: str
    score: float = 0.0
    structured: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def concat_candidates(*lists: list[Candidate]) -> list[Candidate]:
    """POC fusion: simple concatenation with stable ids, no RRF."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for group in lists:
        for c in group:
            key = f"{c.source.value}:{c.id}"
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out
