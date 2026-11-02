"""Document ingest and chunking (FR-KG-01)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agentic_graphrag.config_knowledge import (
    DEFAULT_CHUNK_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_TOKENIZER,
)
from agentic_graphrag.core.ports.parsing import DocumentParser
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.knowledge.chunking import chunk_markdown
from agentic_graphrag.knowledge.parsers import create_parser
from agentic_graphrag.knowledge.parsers.sniff import SUPPORTED_EXTENSIONS


def _doc_id_from_path(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def load_document(path: Path, *, parser: DocumentParser | None = None) -> DocumentRecord:
    parsed = (parser or create_parser()).parse(path.read_bytes(), filename=path.name)
    return DocumentRecord(
        doc_id=_doc_id_from_path(path),
        title=path.stem,
        content=parsed.markdown,
        metadata={
            "source_path": str(path),
            "filename": path.name,
            "suffix": path.suffix.lower(),
            **parsed.metadata(),
        },
    )


def load_documents_from_dir(
    directory: str | Path, *, parser: DocumentParser | None = None
) -> list[DocumentRecord]:
    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"Document directory not found: {root}")
    docs: list[DocumentRecord] = []
    parser = parser or create_parser()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            docs.append(load_document(path, parser=parser))
    return docs


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_OVERLAP_TOKENS,
    tokenizer: str = DEFAULT_TOKENIZER,
) -> list[str]:
    """Compatibility wrapper; size and overlap now count tokens, including context."""
    return [
        chunk.text
        for chunk in chunk_markdown(
            text, chunk_size=chunk_size, overlap=overlap, tokenizer=tokenizer
        )
    ]


def chunk_document(
    doc: DocumentRecord,
    *,
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_OVERLAP_TOKENS,
    tokenizer: str = DEFAULT_TOKENIZER,
) -> list[ChunkRecord]:
    pieces = chunk_markdown(
        doc.content, title=doc.title, chunk_size=chunk_size, overlap=overlap, tokenizer=tokenizer
    )
    return [
        ChunkRecord(
            chunk_id=f"{doc.doc_id}:{i}",
            doc_id=doc.doc_id,
            text=piece.text,
            index=i,
            metadata={
                "title": doc.title,
                **doc.metadata,
                "breadcrumb": piece.breadcrumb,
                "heading_path": list(piece.heading_path),
                "token_count": piece.token_count,
                "tokenizer": piece.tokenizer,
            },
            tenant_id=doc.tenant_id or str(doc.metadata.get("tenant_id") or ""),
        )
        for i, piece in enumerate(pieces)
    ]
