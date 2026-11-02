"""Qdrant tenant aliases permit atomic model/dimension changes during reindex."""

import hashlib
from collections.abc import Callable
from typing import Any

from agentic_graphrag.core.ports.stores import ChunkRecord

MODEL_SWITCH_ATTEMPTS = 3


def tenant_filter(tenant_id: str) -> Any:
    from qdrant_client.http import models as qm

    if tenant_id in {"", "default"}:
        return qm.Filter(
            should=[
                qm.FieldCondition(key="tenant_id", match=qm.MatchAny(any=["", "default"])),
                qm.IsEmptyCondition(is_empty=qm.PayloadField(key="tenant_id")),
            ]
        )
    return qm.Filter(
        must=[qm.FieldCondition(key="tenant_id", match=qm.MatchValue(value=tenant_id))]
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


class QdrantModelRouting:
    _client: Any
    _collection: str
    _routing_enabled = False
    _staging_collection: str | None = None
    _staging_tenant = ""

    def _prefix(self, tenant: str) -> str:
        return f"{self._collection}__tenant_{_digest(tenant or 'default')}"

    def _aliases(self) -> dict[str, str]:
        return {a.alias_name: a.collection_name for a in self._client.get_aliases().aliases}

    def _active_collection(self, tenant: str | None) -> str:
        if not self._routing_enabled or tenant is None:
            return self._collection
        return self._aliases().get(self._prefix(tenant) + "__active", self._collection)

    def _write_collection(self, tenant: str) -> str:
        if self._staging_collection:
            if (tenant or "default") != (self._staging_tenant or "default"):
                raise ValueError("Reindex tenant mismatch")
            return self._staging_collection
        return self._active_collection(tenant)

    def _group_chunks(self, chunks: list[ChunkRecord]) -> dict[str, list[ChunkRecord]]:
        destinations = {
            tenant: self._write_collection(tenant)
            for tenant in {chunk.tenant_id for chunk in chunks}
        }
        groups: dict[str, list[ChunkRecord]] = {}
        for chunk in chunks:
            groups.setdefault(destinations[chunk.tenant_id], []).append(chunk)
        return groups

    def _document_collections(self, tenant: str) -> list[str]:
        names = {c.name for c in self._client.get_collections().collections}
        prefix = self._prefix(tenant) + "__model_"
        return [n for n in names if n == self._collection or n.startswith(prefix)]

    def begin_reindex(self, model: str, *, tenant_id: str) -> None:
        self._staging_tenant = tenant_id
        self._staging_collection = self._prefix(tenant_id) + "__model_" + _digest(model)

    def commit_reindex(self) -> None:
        from qdrant_client.http import models as qm

        target = self._staging_collection
        names = self._document_collections(self._staging_tenant)
        if target is None or target not in names:
            return
        alias = self._prefix(self._staging_tenant) + "__active"
        actions: list[Any] = []
        if alias in self._aliases():
            actions.append(qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=alias)))
        actions.append(
            qm.CreateAliasOperation(
                create_alias=qm.CreateAlias(alias_name=alias, collection_name=target)
            )
        )
        self._client.update_collection_aliases(change_aliases_operations=actions)
        # Publication happens before cleanup; a crash can safely repeat both steps.
        for name in names:
            if name != target:
                self._client.delete(
                    collection_name=name,
                    points_selector=qm.FilterSelector(filter=tenant_filter(self._staging_tenant)),
                    wait=True,
                )

    def search_with_model(
        self, query: str, embed: Callable[[str, str], list[float]], *, top_k: int, tenant_id: str
    ) -> list[tuple[ChunkRecord, float]]:
        for _ in range(MODEL_SWITCH_ATTEMPTS):
            collection = self._active_collection(tenant_id)
            points, _ = self._client.scroll(
                collection_name=collection,
                limit=1,
                scroll_filter=tenant_filter(tenant_id),
                with_payload=["embedding_model"],
                with_vectors=False,
            )
            hits: list[tuple[ChunkRecord, float]] = []
            if points:
                model = str((points[0].payload or {}).get("embedding_model") or "")
                vector = embed(query, model)
                hits = self._search_collection(
                    vector, top_k=top_k, tenant_id=tenant_id, collection=collection
                )
            if self._active_collection(tenant_id) == collection:
                return hits
        raise RuntimeError("Vector model changed repeatedly during query")

    def _search_collection(
        self, query_vector: list[float], *, top_k: int, tenant_id: str | None, collection: str
    ) -> list[tuple[ChunkRecord, float]]:
        raise NotImplementedError

    def end_reindex(self) -> None:
        self._staging_collection = None
