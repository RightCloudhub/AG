"""Chunk-hits to Candidate construction shared by the vector/fulltext retrievers."""

from __future__ import annotations

from collections.abc import Iterable

from agentic_graphrag.core.evidence import Candidate, CandidateSource, Citation
from agentic_graphrag.core.ports.stores import ChunkRecord

CITATION_SPAN_CHARS = 200


def candidates_from_chunk_hits(
    hits: Iterable[tuple[ChunkRecord, float]], *, source: CandidateSource
) -> list[Candidate]:
    """Wrap store chunk hits as Candidates (rank metadata + citation span)."""
    out: list[Candidate] = []
    for rank, (chunk, score) in enumerate(hits):
        out.append(
            Candidate(
                id=chunk.chunk_id,
                source=source,
                content=chunk.text,
                score=float(score),
                structured={"doc_id": chunk.doc_id, "index": chunk.index},
                citations=[
                    Citation(
                        doc_id=chunk.doc_id,
                        chunk_id=chunk.chunk_id,
                        span=chunk.text[:CITATION_SPAN_CHARS],
                        doc_version=chunk.metadata.get("document_version"),
                        content_sha256=chunk.metadata.get("content_sha256"),
                    )
                ],
                metadata={"rank": rank},
            )
        )
    return out
