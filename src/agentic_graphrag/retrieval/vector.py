"""Vector semantic retrieval (FR-RT-01).

The channel has two upstreams — the embedder and the vector store — and both fail
in production (401 from the gateway, Qdrant down). Each failure is recorded before
it propagates so the run reports *which* upstream broke, not just "no evidence".
Model-pinned indexes (``ModelVectorSearch``) embed inside the store call, so the
embedder passed to them is wrapped to keep that attribution.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.ports.capabilities import ModelVectorSearch
from agentic_graphrag.core.ports.llm import LLMClient, ModelEmbedder
from agentic_graphrag.core.ports.stores import ChunkRecord, VectorStore
from agentic_graphrag.observability.degradation import (
    REASON_EMBEDDING_UNAVAILABLE,
    REASON_VECTOR_STORE_UNAVAILABLE,
    ChannelHealth,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.retrieval.chunk_hits import candidates_from_chunk_hits

Hits = Iterable[tuple[ChunkRecord, float]]


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
        if (
            tenant_id is not None
            and isinstance(self.store, ModelVectorSearch)
            and isinstance(self.llm, ModelEmbedder)
        ):
            hits = self._search_with_model(
                query, k, tenant_id=tenant_id, store=self.store, embedder=self.llm
            )
        else:
            vector = self._embed(lambda: self.llm.embed(query))
            hits = self._search_store(
                lambda: self.store.search(vector, top_k=k, tenant_id=tenant_id)
            )
        return candidates_from_chunk_hits(hits, source=CandidateSource.VECTOR_CHUNK)

    def _search_with_model(
        self,
        query: str,
        k: int,
        *,
        tenant_id: str,
        store: ModelVectorSearch,
        embedder: ModelEmbedder,
    ) -> Hits:
        """The store picks the index model and embeds through ``encode``."""
        embed_failures: list[BaseException] = []

        def encode(text: str, model: str) -> list[float]:
            try:
                return self._embed(lambda: embedder.embed_for_model(text, model))
            except Exception as exc:
                embed_failures.append(exc)
                raise

        return self._search_store(
            lambda: store.search_with_model(query, encode, top_k=k, tenant_id=tenant_id),
            recorded=embed_failures,
        )

    def _embed(self, encode: Callable[[], list[float]]) -> list[float]:
        try:
            return encode()
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
        self, run: Callable[[], Hits], *, recorded: Sequence[BaseException] = ()
    ) -> Hits:
        try:
            return run()
        except Exception as exc:  # noqa: BLE001 — vector store unreachable
            # An embedder failure inside a model-pinned search is already attributed.
            if not any(exc is seen for seen in recorded):
                record_degradation(
                    RetrievalChannel.VECTOR,
                    REASON_VECTOR_STORE_UNAVAILABLE,
                    exc,
                    severity=ChannelStatus.FAILED,
                    health=self.health,
                )
            raise
