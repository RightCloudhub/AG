"""Bound fulltext/vector adapters preserve backend model routing and checkpoints."""

from collections.abc import Callable

from agentic_graphrag.core.ports.capabilities import (
    ModelVectorSearch,
    ProcessLocalIndex,
    VectorModelRebuild,
)
from agentic_graphrag.core.ports.stores import ChunkRecord, FulltextStore, VectorStore
from agentic_graphrag.stores.scope_codec import ScopeBinding


class ScopedFulltextStore:
    def __init__(self, store: FulltextStore, binding: ScopeBinding) -> None:
        self.store = store
        self.binding = binding

    @property
    def index_generation(self) -> str:
        origin = (
            self.store.index_generation if isinstance(self.store, ProcessLocalIndex) else "shared"
        )
        return f"{self.binding.owner}:{origin}"

    def index(self, chunks: list[ChunkRecord]) -> int:
        self.binding.check(write=True)
        return self.store.index([self.binding.encode(chunk) for chunk in chunks])

    def search(
        self, query: str, top_k: int = 10, *, tenant_id: str | None = None
    ) -> list[tuple[ChunkRecord, float]]:
        hits = self.store.search(query, top_k, tenant_id=self.binding.check(tenant_id))
        return [(self.binding.decode(chunk), score) for chunk, score in hits]

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        return self.store.delete_by_doc(doc_id, self.binding.check(tenant_id, write=True))

    def clear(self) -> None:
        raise ValueError("Unscoped clear is unavailable on a bound knowledge store")


class ScopedVectorStore:
    def __init__(self, store: VectorStore, binding: ScopeBinding) -> None:
        self.store = store
        self.binding = binding

    @property
    def index_generation(self) -> str:
        origin = (
            self.store.index_generation if isinstance(self.store, ProcessLocalIndex) else "shared"
        )
        return f"{self.binding.owner}:{origin}"

    def ensure_collection(self, dim: int) -> None:
        self.binding.check(write=True)
        self.store.ensure_collection(dim)

    def upsert(self, chunks: list[ChunkRecord]) -> int:
        self.binding.check(write=True)
        return self.store.upsert([self.binding.encode(chunk) for chunk in chunks])

    def search(
        self, query_vector: list[float], top_k: int = 10, *, tenant_id: str | None = None
    ) -> list[tuple[ChunkRecord, float]]:
        hits = self.store.search(query_vector, top_k, tenant_id=self.binding.check(tenant_id))
        return [(self.binding.decode(chunk), score) for chunk, score in hits]

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        return self.store.delete_by_doc(doc_id, self.binding.check(tenant_id, write=True))

    def clear(self) -> None:
        raise ValueError("Unscoped clear is unavailable on a bound knowledge store")

    def close(self) -> None:
        """The owning unbound bundle controls shared client lifetimes."""


class ScopedModelVectorStore(ScopedVectorStore):
    def begin_reindex(self, model: str, *, tenant_id: str) -> None:
        if not isinstance(self.store, VectorModelRebuild):
            raise TypeError("Backend does not support vector model publication")
        self.store.begin_reindex(model, tenant_id=self.binding.check(tenant_id, write=True))

    def commit_reindex(self) -> None:
        self.binding.check(write=True)
        if isinstance(self.store, VectorModelRebuild):
            self.store.commit_reindex()

    def end_reindex(self) -> None:
        if isinstance(self.store, VectorModelRebuild):
            self.store.end_reindex()

    def search_with_model(
        self, query: str, embed: Callable[[str, str], list[float]], *, top_k: int, tenant_id: str
    ) -> list[tuple[ChunkRecord, float]]:
        if not isinstance(self.store, ModelVectorSearch):
            raise TypeError("Backend does not support vector model search")
        hits = self.store.search_with_model(
            query, embed, top_k=top_k, tenant_id=self.binding.check(tenant_id)
        )
        return [(self.binding.decode(chunk), score) for chunk, score in hits]
