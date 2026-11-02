"""Vector semantic retrieval (FR-RT-01).

The channel has two upstreams — the embedder and the vector store — and both fail
in production (401 from the gateway, Qdrant down). Each failure is recorded before
it propagates so the run reports *which* upstream broke, not just "no evidence".
"""

from __future__ import annotations

from collections.abc import Iterable

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.ports.stores import ChunkRecord, VectorStore
from agentic_graphrag.observability.degradation import (
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_VECTOR_STORE_UNAVAILABLE,
    ChannelHealth,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.retrieval.chunk_hits import candidates_from_chunk_hits


class VectorRetriever:
    def __init__(
        self,
        store: VectorStore,
        llm: LLMClient,
        top_k: int = 10,
        *,
        health: ChannelHealth | None = None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.top_k = top_k
        self.health = health

    def search(
        self, query: str, top_k: int | None = None, *, tenant_id: str | None = None
    ) -> list[Candidate]:
        k = top_k or self.top_k
        vector = self._embed(query)
        hits = self._search_store(vector, k, tenant_id=tenant_id)
        return candidates_from_chunk_hits(hits, source=CandidateSource.VECTOR_CHUNK)

    def _embed(self, query: str) -> list[float]:
        try:
            return self.llm.embed(query)
        except Exception as exc:  # noqa: BLE001 — embedder down (401 / unreachable)
            record_degradation(
                RetrievalChannel.EMBEDDING,
                REASON_EMBEDDING_UNAVAILABLE,
                exc,
                severity=ChannelStatus.FAILED,
                health=self.health,
            )
            # No embedding means no vector search at all: the vector channel is
            # down too, and its reason should point at the real cause.
            if self.health is not None:
                self.health.mark(
                    RetrievalChannel.VECTOR, ChannelStatus.FAILED, REASON_EMBEDDING_UNAVAILABLE
                )
            raise

    def _search_store(
        self, vector: list[float], k: int, *, tenant_id: str | None
    ) -> Iterable[tuple[ChunkRecord, float]]:
        try:
            return self.store.search(vector, top_k=k, tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001 — vector store unreachable
            record_degradation(
                RetrievalChannel.VECTOR,
                REASON_VECTOR_STORE_UNAVAILABLE,
                exc,
                severity=ChannelStatus.FAILED,
                health=self.health,
            )
            raise
