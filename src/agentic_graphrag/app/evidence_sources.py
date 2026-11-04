"""Resolve citations against server-owned, versioned original chunks."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.evidence import Citation
from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.governance.source_store import SourceStore
from agentic_graphrag.observability.degradation import record_degradation

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService
    from agentic_graphrag.core.reasoning import ReasoningChain

type DocumentCache = dict[str, DocumentRecord | None]


def source_store(svc: QueryService) -> SourceStore:
    root = svc.parent or svc
    path = runtime_dir(svc.cfg) / "sources.sqlite3"
    with root._lock:
        if root.source_archive is None or root.source_archive.path != path:
            root.source_archive = SourceStore(path)
        return root.source_archive


def query_scope(svc: QueryService, *, tenant_id: str = "default") -> KnowledgeScope:
    return svc.knowledge_base.scope if svc.knowledge_base else KnowledgeScope(tenant_id=tenant_id)


def resolve_citation(
    svc: QueryService,
    citation: Citation,
    *,
    scope: KnowledgeScope,
    documents: DocumentCache | None = None,
) -> tuple[SourceRef, str] | None:
    doc = _citation_document(
        svc, citation, scope=scope, documents=documents if documents is not None else {}
    )
    if doc is None:
        return None
    chunk = next((c for c in doc.chunks if c.chunk_id == citation.chunk_id), None)
    if chunk is None:
        return None
    start = doc.content.find(chunk.text)
    ref = SourceRef(
        scope=scope,
        doc_id=doc.doc_id,
        doc_version=doc.version,
        chunk_id=chunk.chunk_id,
        content_sha256=_document_hash(doc),
        span_start=max(start, 0),
        span_end=max(start, 0) + len(chunk.text),
        span_origin="document" if start >= 0 else "chunk",
        paragraph=str(chunk.metadata.get("breadcrumb") or "") or None,
    )
    return ref, chunk.text


def _citation_document(
    svc: QueryService,
    citation: Citation,
    *,
    scope: KnowledgeScope,
    documents: DocumentCache,
) -> DocumentRecord | None:
    if not citation.doc_id or not citation.chunk_id:
        return None
    if citation.doc_id not in documents:
        documents[citation.doc_id] = svc.bundle.docs.get(citation.doc_id, tenant_id=scope.tenant_id)
    doc = documents[citation.doc_id]
    if doc is None or doc.metadata.get("lifecycle"):
        return None
    if doc.version != (citation.doc_version or 1):
        return None
    digest = hashlib.sha256(doc.content.encode()).hexdigest()
    if citation.content_sha256 and citation.content_sha256 != digest:
        return None
    return doc


def publish_evidence(
    svc: QueryService, entry: dict[str, Any], *, scope: KnowledgeScope
) -> dict[str, Any]:
    return publish_evidence_batch(svc, [entry], scope=scope)[0]


def publish_evidence_batch(
    svc: QueryService, entries: list[dict[str, Any]], *, scope: KnowledgeScope
) -> list[dict[str, Any]]:
    if not entries:
        return []
    # Repeated graph paths often cite the same original. Cache reads, including
    # misses, only within this publication; subsequent requests recheck storage.
    documents: DocumentCache = {}
    rows = [_prepare_evidence(svc, entry, scope=scope, documents=documents) for entry in entries]
    persisted = True
    try:
        source_store(svc).put_many(
            scope, [(row["evidence_id"], row) for row in rows], kind="evidence"
        )
    except Exception as exc:
        record_degradation("source_audit", "audit_write_failed", exc)
        persisted = False
    return [
        {
            **entry,
            "evidence_id": row["evidence_id"],
            "source_refs": row["source_refs"],
            "source_status": row["status"] if persisted else "unavailable",
            "source_audit_status": "persisted" if persisted else "failed",
        }
        for entry, row in zip(entries, rows, strict=True)
    ]


def _prepare_evidence(
    svc: QueryService,
    entry: dict[str, Any],
    *,
    scope: KnowledgeScope,
    documents: DocumentCache,
) -> dict[str, Any]:
    resolved = _resolve_sources(svc, entry, scope=scope, documents=documents)
    refs = list({ref.model_dump_json(): ref for ref, _ in resolved}.values())
    content = "\n\n".join(dict.fromkeys(text for _, text in resolved))
    identity = json.dumps(
        [
            scope.model_dump(),
            entry["id"],
            [r.model_dump() for r in refs],
            entry.get("citations", []),
            content or entry.get("content", ""),
        ],
        sort_keys=True,
    )
    key = "ev_" + hashlib.sha256(identity.encode()).hexdigest()
    return {
        "evidence_id": key,
        "content": content or entry.get("content", ""),
        "source_refs": [ref.model_dump(mode="json") for ref in refs],
        "citations": entry.get("citations", []),
        "status": "available" if refs else "unavailable",
    }


def _resolve_sources(
    svc: QueryService,
    entry: dict[str, Any],
    *,
    scope: KnowledgeScope,
    documents: DocumentCache,
) -> list[tuple[SourceRef, str]]:
    from agentic_graphrag.generation.global_verifier import original_excerpt

    if entry.get("provenance"):
        refs = [SourceRef.model_validate(raw) for raw in entry["provenance"]]
        return [(ref, original_excerpt(ref, svc.bundle.docs, scope=scope)) for ref in refs]
    return [
        value
        for raw in entry.get("citations", [])
        if (
            value := resolve_citation(
                svc, Citation.model_validate(raw), scope=scope, documents=documents
            )
        )
    ]


def publish_chain_sources(svc: QueryService, chain: ReasoningChain) -> bool:
    scope = query_scope(svc, tenant_id=str(chain.metadata.get("tenant_id") or "default"))
    catalog = chain.metadata.get("evidence") or []
    chain.metadata["evidence"] = publish_evidence_batch(svc, catalog, scope=scope)
    if svc.snapshot is not None:
        chain.metadata["snapshot"] = svc.snapshot.model_dump(mode="json")
    return all(row["source_audit_status"] == "persisted" for row in chain.metadata["evidence"])


def source_visible(svc: QueryService, source: SourceRef, *, current: bool = False) -> bool:
    if source.scope != query_scope(svc, tenant_id=source.scope.tenant_id):
        return False
    if svc.knowledge_store and svc.knowledge_store.is_tombstoned(
        source.scope, source.doc_id, version=source.doc_version
    ):
        return False
    if not current:
        return True
    doc = svc.bundle.docs.get(source.doc_id, tenant_id=source.scope.tenant_id)
    return bool(
        doc
        and doc.version == source.doc_version
        and not doc.metadata.get("lifecycle")
        and _document_hash(doc) == source.content_sha256
    )


def _document_hash(doc: DocumentRecord) -> str:
    return doc.content_sha256 or hashlib.sha256(doc.content.encode()).hexdigest()


def catalog_visible(svc: QueryService, catalog: list[dict[str, Any]], *, current: bool) -> bool:
    if current and any(row.get("citations") and not row.get("source_refs") for row in catalog):
        return False
    if not _citations_visible(svc, catalog):
        return False
    return all(
        source_visible(svc, SourceRef.model_validate(raw), current=current)
        for item in catalog
        for raw in item.get("source_refs", [])
    )


def _citations_visible(svc: QueryService, catalog: list[dict[str, Any]]) -> bool:
    if svc.knowledge_store is None:
        return True
    scope = query_scope(svc)
    return not any(
        svc.knowledge_store.is_tombstoned(scope, citation.doc_id, version=citation.doc_version or 1)
        for item in catalog
        for raw in item.get("citations", [])
        if (citation := Citation.model_validate(raw)).doc_id
    )
