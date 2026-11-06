"""Source validation and stable mention IDs before open entities can be stored."""

import hashlib
import json

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.open_contracts import OpenSpan


def source_ref(chunk: ChunkRecord, span: OpenSpan, *, scope: KnowledgeScope) -> SourceRef:
    if (chunk.tenant_id or "default") != scope.tenant_id or chunk.kb_id != scope.kb_id:
        raise ValueError("Extraction chunk belongs to another knowledge scope")
    start = span.start
    if start is None:
        if chunk.text.count(span.quote) != 1:
            raise ValueError("An ambiguous source quote requires an explicit offset")
        start = chunk.text.index(span.quote)
    end = start + len(span.quote)
    if chunk.text[start:end] != span.quote:
        raise ValueError("Extracted evidence is not an exact original-text span")
    return SourceRef(
        scope=scope,
        doc_id=chunk.doc_id,
        doc_version=int(chunk.metadata.get("document_version", 0)),
        chunk_id=chunk.chunk_id,
        content_sha256=str(chunk.metadata.get("content_sha256", "")),
        span_origin="chunk",
        span_start=start,
        span_end=end,
        page=chunk.metadata.get("page"),
    )


def mention_id(source: SourceRef, ref: str) -> str:
    payload = [source.model_dump(mode="json"), ref]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"mention-{digest[:32]}"


def canonical_id(mention: str) -> str:
    return f"entity-{hashlib.sha256(mention.encode()).hexdigest()[:32]}"
