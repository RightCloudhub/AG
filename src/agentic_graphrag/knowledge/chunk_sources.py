"""Attach server-derived document versions and normalized content hashes to chunks."""

import hashlib

from agentic_graphrag.core.ports.stores import DocumentRecord


def stamp_chunk_sources(doc: DocumentRecord) -> None:
    digest = hashlib.sha256(doc.content.encode("utf-8")).hexdigest()
    doc.content_sha256 = digest
    for chunk in doc.chunks:
        chunk.kb_id = doc.kb_id
        chunk.generation = doc.generation
        chunk.metadata.update(
            document_version=doc.version,
            content_sha256=digest,
            kb_id=doc.kb_id,
            generation=doc.generation,
        )
