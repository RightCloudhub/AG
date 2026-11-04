"""Remove document provenance without discarding facts supported elsewhere."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from agentic_graphrag.core.ports.stores import ChunkIdentity, ChunkKey, RelationRecord
from agentic_graphrag.core.tenancy import tenant_matches


def obsolete_support(source: dict[str, Any], key: ChunkKey, below: ChunkIdentity | None) -> bool:
    """True for a provenance entry of ``key``'s chunk older than ``below`` (or any)."""
    if source.get("chunk_id") != key.chunk_id:
        return False
    if source.get("doc_id") and source.get("doc_id") != key.doc_id:
        return False
    return below is None or ChunkIdentity.of_source(source) < below


def merge_sources(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = list(old)
    for source in new:
        if source not in result:
            result.append(source)
    return result


class MemoryDocumentDeletion:
    _relations: dict[str, RelationRecord]
    _by_name: dict[str, list[str]] | None

    def delete_by_doc(self, doc_id: str, tenant_id: str) -> int:
        return self._drop_sources(tenant_id, lambda s: s.get("doc_id") == doc_id)

    def delete_chunk_support(self, key: ChunkKey, *, below: ChunkIdentity | None = None) -> int:
        """Remove one chunk's provenance; relations still supported elsewhere survive."""
        return self._drop_sources(key.tenant_id, lambda s: obsolete_support(s, key, below))

    def _drop_sources(self, tenant_id: str, doomed: Callable[[dict[str, Any]], bool]) -> int:
        deleted = 0
        for key, relation in list(self._relations.items()):
            if not tenant_matches(relation.tenant_id, tenant_id):
                continue
            sources = [s for s in relation.sources if not doomed(s)]
            if len(sources) == len(relation.sources):
                continue
            if sources:
                self._relations[key] = replace(relation, sources=sources)
            else:
                del self._relations[key]
                deleted += 1
        self._by_name = None
        return deleted
