"""A document adapter that binds every operation, including deduplication, to one KB."""

from contextlib import AbstractContextManager

from agentic_graphrag.core.ports.stores import ChunkRecord, DocStore, DocumentRecord
from agentic_graphrag.stores.scope_codec import ScopeBinding


class ScopedDocStore:
    def __init__(self, store: DocStore, binding: ScopeBinding) -> None:
        self.store = store
        self.binding = binding
        self.durable = store.durable

    def lock(self) -> AbstractContextManager[None]:
        return self.store.lock()

    def save(self, doc: DocumentRecord) -> None:
        self.store.save(self.binding.encode(doc))

    def save_chunks(self, doc: DocumentRecord, chunks: list[ChunkRecord]) -> None:
        self.store.save_chunks(
            self.binding.encode(doc), [self.binding.encode(chunk) for chunk in chunks]
        )

    def get(self, doc_id: str, *, tenant_id: str | None = None) -> DocumentRecord | None:
        doc = self.store.get(doc_id, tenant_id=self.binding.check(tenant_id))
        return self.binding.decode(doc) if doc is not None else None

    def list_ids(self, *, tenant_id: str | None = None) -> list[str]:
        return self.store.list_ids(tenant_id=self.binding.check(tenant_id))

    def list_refs(self, *, tenant_id: str | None = None) -> list[tuple[str, str]]:
        return [
            (doc_id, self.binding.scope.tenant_id) for doc_id in self.list_ids(tenant_id=tenant_id)
        ]

    def delete(self, doc_id: str, *, tenant_id: str) -> None:
        self.store.delete(doc_id, tenant_id=self.binding.check(tenant_id, write=True))

    def find(self, value: str, *, tenant_id: str, by_hash: bool = False) -> DocumentRecord | None:
        doc = self.store.find(value, tenant_id=self.binding.check(tenant_id), by_hash=by_hash)
        return self.binding.decode(doc) if doc is not None else None
