"""One recoverable replacement/deletion path for uploads, CLI and reconciliation."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from agentic_graphrag.config import resolve_path
from agentic_graphrag.core.ports.document_ops_store import LeaseStore
from agentic_graphrag.core.ports.stores import (
    DocStore,
    DocumentRecord,
    FulltextStore,
    GraphStore,
    VectorStore,
)


class DocumentLifecycle:
    def __init__(
        self,
        docs: DocStore,
        *,
        vector: VectorStore,
        fulltext: FulltextStore | None = None,
        graph: GraphStore | None = None,
    ) -> None:
        self.docs, self.vector, self.fulltext, self.graph = docs, vector, fulltext, graph

    def accept(self, incoming: DocumentRecord, *, reserved_id: str | None = None) -> dict[str, str]:
        """Hash dedup within a tenant; same source replaces the stable UUID's version.

        ``reserved_id`` is the identity an async parse reserved for a new document.
        """
        reset_index_state(incoming)
        incoming.tenant_id = incoming.tenant_id or "default"
        incoming.content_sha256 = hashlib.sha256(incoming.content.encode("utf-8")).hexdigest()
        incoming.source_uri = incoming.source_uri or f"upload://{incoming.title}"
        with self.docs.lock():
            duplicate = self.docs.find(
                incoming.content_sha256, tenant_id=incoming.tenant_id, by_hash=True
            )
            if duplicate and not duplicate.metadata.get("lifecycle"):
                return document_row(duplicate, "duplicate")
            previous = self.docs.find(incoming.source_uri, tenant_id=incoming.tenant_id)
            if previous and previous.metadata.get("lifecycle"):
                previous = self.resume(previous)
            incoming.doc_id = previous.doc_id if previous else (reserved_id or str(uuid4()))
            incoming.version = previous.version + 1 if previous else 1
            incoming.updated_at = datetime.now(UTC).isoformat()
            incoming.created_at = (previous.created_at if previous else "") or incoming.updated_at
            if previous:
                self._journal_replacement(previous, incoming)
            else:
                self.docs.save(incoming)
                self._changed(incoming.tenant_id)
            return document_row(incoming, "queued")

    def force_replace(
        self, doc: DocumentRecord, *, content: str, metadata: dict[str, Any]
    ) -> DocumentRecord:
        """Explicit reparse: same UUID, version + 1, rebuilt even when text is unchanged.

        Bypasses hash dedup and uses the journaled replacement, so a crash after the
        intent commits is completed by ``resume``. Callers hold ``docs.lock()``.
        """
        now = datetime.now(UTC).isoformat()
        kept = {k: v for k, v in doc.metadata.items() if k not in _LIFECYCLE_KEYS}
        replacement = DocumentRecord(
            doc_id=doc.doc_id,
            title=doc.title,
            content=content,
            metadata={**kept, **metadata},
            tenant_id=doc.tenant_id or "default",
            source_uri=doc.source_uri,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            version=doc.version + 1,
            created_at=doc.created_at or now,
            updated_at=now,
        )
        reset_index_state(replacement)
        self._journal_replacement(doc, replacement)
        return replacement

    def _journal_replacement(self, previous: DocumentRecord, incoming: DocumentRecord) -> None:
        previous.metadata["lifecycle"] = "replacing"
        previous.metadata["replacement"] = asdict(incoming)
        self.docs.save(previous)
        self._changed(previous.tenant_id)
        self.resume(previous)

    def _changed(self, tenant_id: str) -> None:
        """Publish a shared generation bump on lifecycle intent (C-04)."""
        if isinstance(self.docs, LeaseStore):
            self.docs.bump_generation(tenant_id or "default")

    def clear_indexes(self, doc: DocumentRecord) -> None:
        self.vector.delete_by_doc(doc.doc_id, doc.tenant_id)
        if self.fulltext is not None:
            self.fulltext.delete_by_doc(doc.doc_id, doc.tenant_id)
        if self.graph is not None:
            self.graph.delete_by_doc(doc.doc_id, doc.tenant_id)

    def delete(self, doc_id: str, *, tenant_id: str) -> None:
        with self.docs.lock():
            doc = self.docs.get(doc_id, tenant_id=tenant_id)
            if doc is None:
                raise KeyError(doc_id)
            doc.metadata["lifecycle"] = "deleting"
            doc.metadata.pop("replacement", None)
            self.docs.save(doc)
            self._changed(tenant_id)
            self.resume(doc)

    def resume(self, doc: DocumentRecord) -> DocumentRecord | None:
        """Idempotent cleanup; intent remains durable if any backend fails."""
        action = doc.metadata.get("lifecycle")
        if not action:
            return doc
        self.clear_indexes(doc)
        if action == "deleting":
            self.docs.delete(doc.doc_id, tenant_id=doc.tenant_id)
            return None
        replacement = DocumentRecord(**doc.metadata["replacement"])
        self.docs.save(replacement)
        if replacement.doc_id != doc.doc_id:
            self.docs.delete(doc.doc_id, tenant_id=doc.tenant_id)
        return replacement

    def migrate(self, *, tenant_id: str) -> list[dict[str, str]]:
        """Convert legacy IDs, preserving text; cleanup is journaled and restartable."""
        rows = []
        with self.docs.lock():
            for did in self.docs.list_ids(tenant_id=tenant_id):
                doc = self.docs.get(did, tenant_id=tenant_id)
                if doc is not None:
                    rows.append(self._migrate_one(doc))
        return rows

    def _migrate_one(self, doc: DocumentRecord) -> dict[str, str]:
        old_id = doc.doc_id
        if doc.metadata.get("lifecycle"):
            resumed = self.resume(doc)
            return {"old_id": old_id, "doc_id": resumed.doc_id if resumed else ""}
        try:
            UUID(old_id)
            return {"old_id": str(doc.metadata.get("legacy_doc_id", old_id)), "doc_id": old_id}
        except ValueError:
            pass
        now = datetime.now(UTC).isoformat()
        replacement = DocumentRecord(
            doc_id=str(uuid4()),
            title=doc.title,
            content=doc.content,
            tenant_id=doc.tenant_id or "default",
            metadata={**doc.metadata, "legacy_doc_id": old_id},
            source_uri=doc.source_uri or _legacy_source(doc),
            content_sha256=hashlib.sha256(doc.content.encode("utf-8")).hexdigest(),
            version=doc.version,
            created_at=doc.created_at or now,
            updated_at=now,
        )
        reset_index_state(replacement)
        doc.metadata.update(lifecycle="replacing", replacement=asdict(replacement))
        self.docs.save(doc)
        self.resume(doc)
        return {"old_id": old_id, "doc_id": replacement.doc_id}


def document_row(doc: DocumentRecord, status: str) -> dict[str, str]:
    return {
        "doc_id": doc.doc_id,
        "version": str(doc.version),
        "status": status,
        "bytes": str(len(doc.content.encode("utf-8"))),
        "name": doc.title,
        "source_uri": doc.source_uri,
        "content_sha256": doc.content_sha256,
    }


def _legacy_source(doc: DocumentRecord) -> str:
    source = doc.metadata.get("source_path")
    return resolve_path(str(source)).as_uri() if source else f"upload://{doc.doc_id}"


_LIFECYCLE_KEYS = (
    "chunks_initialized",
    "index_complete",
    "index_error",
    "index_generations",
    "lifecycle",
    "replacement",
)


def reset_index_state(doc: DocumentRecord) -> None:
    doc.chunks = []
    for key in _LIFECYCLE_KEYS:
        doc.metadata.pop(key, None)
