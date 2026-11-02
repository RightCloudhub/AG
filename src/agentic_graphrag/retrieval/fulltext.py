"""BM25 fulltext retrieval (FR-RT-03)."""

from __future__ import annotations

from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.stores import FulltextStore
from agentic_graphrag.retrieval.chunk_hits import candidates_from_chunk_hits


class FulltextRetriever:
    def __init__(self, store: FulltextStore, top_k: int = 10) -> None:
        self.store = store
        self.top_k = top_k

    def search(
        self, query: str, top_k: int | None = None, *, tenant_id: str | None = None
    ) -> list[Candidate]:
        k = top_k or self.top_k
        hits = self.store.search(query, top_k=k, tenant_id=tenant_id)
        return candidates_from_chunk_hits(hits, source=CandidateSource.FULLTEXT_CHUNK)
