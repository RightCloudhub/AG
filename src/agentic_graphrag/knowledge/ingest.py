"""Document ingest and chunking (FR-KG-01)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from agentic_graphrag.stores.interfaces import ChunkRecord, DocumentRecord

_SUPPORTED = {".md", ".txt", ".html", ".htm"}


def _doc_id_from_path(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def load_document(path: Path) -> DocumentRecord:
    text = path.read_text(encoding="utf-8", errors="replace")
    # Strip simple HTML tags if needed
    if path.suffix.lower() in {".html", ".htm"}:
        text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return DocumentRecord(
        doc_id=_doc_id_from_path(path),
        title=path.stem,
        content=text,
        metadata={
            "source_path": str(path),
            "filename": path.name,
            "suffix": path.suffix.lower(),
        },
    )


def load_documents_from_dir(directory: str | Path) -> list[DocumentRecord]:
    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"Document directory not found: {root}")
    docs: list[DocumentRecord] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in _SUPPORTED:
            docs.append(load_document(path))
    return docs


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1200,
    overlap: int = 150,
) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = max(0, min(overlap, chunk_size - 1))
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        # Prefer break at paragraph/sentence boundary
        if end < n:
            window = text[start:end]
            break_at = max(window.rfind("\n\n"), window.rfind("。"), window.rfind(". "))
            if break_at > chunk_size // 3:
                end = start + break_at + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_document(
    doc: DocumentRecord,
    *,
    chunk_size: int = 1200,
    overlap: int = 150,
) -> list[ChunkRecord]:
    pieces = chunk_text(doc.content, chunk_size=chunk_size, overlap=overlap)
    return [
        ChunkRecord(
            chunk_id=f"{doc.doc_id}:{i}",
            doc_id=doc.doc_id,
            text=piece,
            index=i,
            metadata={"title": doc.title, **doc.metadata},
        )
        for i, piece in enumerate(pieces)
    ]
