"""BM25 fulltext retrieval (FR-RT-03)."""

from __future__ import annotations

from collections.abc import Iterable

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.stores import ChunkRecord, FulltextStore
from agentic_graphrag.observability.degradation import (
    REASON_FULLTEXT_UNAVAILABLE,
    ChannelHealth,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.retrieval.chunk_hits import candidates_from_chunk_hits


class FulltextRetriever:
    def __init__(
        self,
        store: FulltextStore,
        top_k: int = 10,
        *,
        health: ChannelHealth | None = None,
    ) -> None:
        self.store = store
        self.top_k = top_k
        self.health = health

    def search(
        self, query: str, top_k: int | None = None, *, tenant_id: str | None = None
    ) -> list[Candidate]:
        k = top_k or self.top_k
        hits = self._search_store(query, k, tenant_id=tenant_id)
        return candidates_from_chunk_hits(hits, source=CandidateSource.FULLTEXT_CHUNK)

    def _search_store(
        self, query: str, k: int, *, tenant_id: str | None
    ) -> Iterable[tuple[ChunkRecord, float]]:
        try:
            return self.store.search(query, top_k=k, tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001 — fulltext index unreadable
            record_degradation(
                RetrievalChannel.FULLTEXT,
                REASON_FULLTEXT_UNAVAILABLE,
                exc,
                severity=ChannelStatus.FAILED,
                health=self.health,
            )
            raise
