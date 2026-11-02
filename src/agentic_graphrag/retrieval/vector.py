"""Vector semantic retrieval (FR-RT-01)."""

from __future__ import annotations

from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.ports.stores import VectorStore
from agentic_graphrag.retrieval.chunk_hits import candidates_from_chunk_hits


class VectorRetriever:
    def __init__(self, store: VectorStore, llm: LLMClient, top_k: int = 10) -> None:
        self.store = store
        self.llm = llm
        self.top_k = top_k

    def search(
        self, query: str, top_k: int | None = None, *, tenant_id: str | None = None
    ) -> list[Candidate]:
        k = top_k or self.top_k
        vector = self.llm.embed(query)
        hits = self.store.search(vector, top_k=k, tenant_id=tenant_id)
        return candidates_from_chunk_hits(hits, source=CandidateSource.VECTOR_CHUNK)
