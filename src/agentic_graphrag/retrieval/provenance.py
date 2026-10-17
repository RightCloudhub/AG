"""Edge → source-chunk provenance for graph candidates (ARCH_OPTIMIZATION_V2 O-6).

``RelationRecord.sources`` already carries ``{doc_id, chunk_id, span}`` written
at graph-build time (``knowledge/graph_builder.py``) and persisted by Neo4j.
Graph retrieval used to drop it; these helpers surface it on the candidate so
generation / NLI can check a graph fact against its original text.
"""

from __future__ import annotations

from collections.abc import Iterable

from agentic_graphrag.core.evidence import Citation
from agentic_graphrag.core.ports.stores import RelationRecord

MAX_SOURCES_PER_EDGE = 3


def source_citations(relations: Iterable[RelationRecord]) -> list[Citation]:
    """Unique (doc_id, chunk_id) citations for ``relations``, in order."""
    out: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for rel in relations:
        for cite in _edge_citations(rel):
            key = (cite.doc_id or "", cite.chunk_id or "")
            if key not in seen:
                seen.add(key)
                out.append(cite)
    return out


def _edge_citations(rel: RelationRecord) -> list[Citation]:
    return [
        Citation(
            doc_id=str(src.get("doc_id") or "") or None,
            chunk_id=str(src["chunk_id"]),
            relation_id=rel.id or None,
            span=src.get("span") or None,
        )
        for src in list(rel.sources or [])[:MAX_SOURCES_PER_EDGE]
        if src.get("chunk_id")
    ]


def source_chunk_ids(citations: Iterable[Citation]) -> list[str]:
    return [c.chunk_id for c in citations if c.chunk_id]
