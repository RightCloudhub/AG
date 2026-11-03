"""Remove document provenance without discarding facts supported elsewhere."""

from dataclasses import replace
from typing import Any

from agentic_graphrag.core.ports.stores import RelationRecord
from agentic_graphrag.core.tenancy import tenant_matches


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
        deleted = 0
        for key, relation in list(self._relations.items()):
            if not tenant_matches(relation.tenant_id, tenant_id):
                continue
            sources = [s for s in relation.sources if s.get("doc_id") != doc_id]
            if len(sources) == len(relation.sources):
                continue
            if sources:
                self._relations[key] = replace(relation, sources=sources)
            else:
                del self._relations[key]
                deleted += 1
        self._by_name = None
        return deleted
