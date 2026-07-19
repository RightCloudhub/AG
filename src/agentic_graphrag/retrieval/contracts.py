"""Unified retrieval candidate contract (P2-RT-02 / FR-RT-01~04).

All retrieval tools return the same shape for fusion, Agent memory, citation
binding (AG-05), and eval evidence recall (EV-04):

| field      | meaning                                              |
|------------|------------------------------------------------------|
| type       | vector_chunk / graph_path / graph_neighbor / fulltext_chunk |
| content    | text or natural-language path description            |
| structured | graph payloads (nodes/edges) when applicable         |
| score      | within-channel score (not cross-channel comparable)  |
| citations  | source refs (doc_id+span, or entity/relation ids)    |

``source`` is kept as the storage field; ``type`` is the contract alias
(computed). Coarse legacy values (``vector`` / ``graph`` / ``fulltext``) are
accepted on input and normalized to fine-grained types.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator


class CandidateSource(StrEnum):
    """Fine-grained candidate type (workstream §2).

    Coarse members (VECTOR/GRAPH/FULLTEXT) remain for backward-compatible
    *comparisons* after normalization; new code should use the ``_*_CHUNK`` /
    ``GRAPH_*`` values and prefer :meth:`Candidate.channel` for channel checks.
    """

    VECTOR_CHUNK = "vector_chunk"
    GRAPH_PATH = "graph_path"
    GRAPH_NEIGHBOR = "graph_neighbor"
    FULLTEXT_CHUNK = "fulltext_chunk"
    FUSION = "fusion"
    # Coarse / legacy (normalized on Candidate construction)
    VECTOR = "vector"
    GRAPH = "graph"
    FULLTEXT = "fulltext"


# Alias matching the workstream field name ``type``
CandidateType = CandidateSource

Channel = Literal["vector", "graph", "fulltext", "fusion"]

_SOURCE_NORMALIZE: dict[str, CandidateSource] = {
    "vector": CandidateSource.VECTOR_CHUNK,
    "vector_chunk": CandidateSource.VECTOR_CHUNK,
    "fulltext": CandidateSource.FULLTEXT_CHUNK,
    "fulltext_chunk": CandidateSource.FULLTEXT_CHUNK,
    "graph": CandidateSource.GRAPH_NEIGHBOR,  # default when kind unknown
    "graph_neighbor": CandidateSource.GRAPH_NEIGHBOR,
    "graph_path": CandidateSource.GRAPH_PATH,
    "fusion": CandidateSource.FUSION,
}

_CHANNEL: dict[CandidateSource, Channel] = {
    CandidateSource.VECTOR_CHUNK: "vector",
    CandidateSource.VECTOR: "vector",
    CandidateSource.FULLTEXT_CHUNK: "fulltext",
    CandidateSource.FULLTEXT: "fulltext",
    CandidateSource.GRAPH_PATH: "graph",
    CandidateSource.GRAPH_NEIGHBOR: "graph",
    CandidateSource.GRAPH: "graph",
    CandidateSource.FUSION: "fusion",
}


def normalize_source(
    source: CandidateSource | str,
    *,
    kind: str | None = None,
) -> CandidateSource:
    """Normalize a source string/enum to a fine-grained :class:`CandidateSource`.

    ``kind`` disambiguates coarse ``graph`` → ``graph_path`` vs ``graph_neighbor``.
    """
    raw = source.value if isinstance(source, CandidateSource) else str(source)
    key = raw.strip().lower()
    if key == "graph" and kind:
        k = kind.strip().lower()
        if k in {"path", "graph_path"}:
            return CandidateSource.GRAPH_PATH
        if k in {"neighbor", "graph_neighbor", "nbr"}:
            return CandidateSource.GRAPH_NEIGHBOR
    if key in _SOURCE_NORMALIZE:
        return _SOURCE_NORMALIZE[key]
    # Unknown → try enum construction; fall back to fusion for safety
    try:
        return CandidateSource(key)
    except ValueError:
        return CandidateSource.FUSION


def channel_of(source: CandidateSource | str) -> Channel:
    """Return the coarse retrieval channel for a source value."""
    src = source if isinstance(source, CandidateSource) else normalize_source(source)
    return _CHANNEL.get(src, "fusion")


def is_graph_source(source: CandidateSource | str) -> bool:
    return channel_of(source) == "graph"


class Citation(BaseModel):
    """Source pointer for a candidate (doc span and/or graph element)."""

    doc_id: str | None = None
    chunk_id: str | None = None
    entity_id: str | None = None
    relation_id: str | None = None
    span: str | None = None


class Candidate(BaseModel):
    """Unified retrieval hit consumed by fusion, memory, generation, and eval."""

    id: str
    source: CandidateSource
    content: str
    score: float = 0.0
    structured: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source_field(cls, v: Any) -> CandidateSource:
        if isinstance(v, CandidateSource):
            # Re-normalize coarse members to fine-grained
            return normalize_source(v)
        return normalize_source(str(v))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def type(self) -> str:
        """Contract field name from retrieval.md (alias of ``source``)."""
        return self.source.value

    @property
    def channel(self) -> Channel:
        """Coarse channel: vector | graph | fulltext | fusion.

        After RRF, ``source`` may be ``fusion`` — recover origin channel from
        ``structured.origin_source`` / ``origins`` so Agent/eval still see
        graph vs text correctly.
        """
        ch = channel_of(self.source)
        if ch != "fusion":
            return ch
        origin = (self.structured or {}).get("origin_source")
        if origin:
            return channel_of(str(origin))
        origins = (self.structured or {}).get("origins") or []
        if origins and isinstance(origins[0], dict) and origins[0].get("source"):
            return channel_of(str(origins[0]["source"]))
        return ch

    def is_graph(self) -> bool:
        return self.channel == "graph"

    def citation_keys(self) -> list[str]:
        """Stable citation keys for evidence-recall scoring."""
        keys: list[str] = []
        for c in self.citations:
            if c.chunk_id:
                keys.append(f"chunk:{c.chunk_id}")
            if c.doc_id and not c.chunk_id:
                keys.append(f"doc:{c.doc_id}")
            if c.entity_id:
                keys.append(f"entity:{c.entity_id}")
            if c.relation_id:
                keys.append(f"relation:{c.relation_id}")
        if not keys:
            keys.append(f"candidate:{self.id}")
        return keys


def concat_candidates(*lists: list[Candidate]) -> list[Candidate]:
    """POC fusion: simple concatenation with stable ids, no RRF.

    Dedupes on ``(channel, id)`` so the same chunk from vector and fulltext
    can both appear (different channels), while true duplicates drop.
    """
    seen: set[str] = set()
    out: list[Candidate] = []
    for group in lists:
        for c in group:
            key = f"{c.channel}:{c.id}"
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out


def rrf_fuse(
    *ranked_lists: list[Candidate],
    k: int = 60,
    limit: int | None = None,
) -> list[Candidate]:
    """Reciprocal Rank Fusion across channel-ranked lists (FR-RT-04 prep).

    Score is purely rank-based so heterogeneous channel scores never mix.
    Returns candidates with ``source=FUSION`` and original payload preserved
    in ``structured["origins"]``.
    """
    scores: dict[str, float] = {}
    best: dict[str, Candidate] = {}
    origins: dict[str, list[dict[str, Any]]] = {}

    for group in ranked_lists:
        for rank, c in enumerate(group):
            key = f"{c.channel}:{c.id}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in best or c.score > best[key].score:
                best[key] = c
            origins.setdefault(key, []).append(
                {"source": c.source.value, "score": c.score, "rank": rank}
            )

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if limit is not None:
        ordered = ordered[:limit]

    fused: list[Candidate] = []
    for key, rrf_score in ordered:
        base = best[key]
        fused.append(
            Candidate(
                id=base.id,
                source=CandidateSource.FUSION,
                content=base.content,
                score=rrf_score,
                structured={
                    **base.structured,
                    "origins": origins[key],
                    "origin_source": base.source.value,
                },
                citations=list(base.citations),
                metadata={**base.metadata, "rrf_k": k},
            )
        )
    return fused
