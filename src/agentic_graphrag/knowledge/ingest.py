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


_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1200,
    overlap: int = 150,
) -> list[str]:
    """Heading-aware chunking (D6).

    Markdown headings are hard boundaries: a chunk never spans two sections,
    so retrieved evidence stays inside one topic. Sections larger than the
    window fall back to the size/overlap windower with paragraph/sentence
    breaks. Heading-less text behaves exactly like the old single-window path.
    """
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    sections = _split_sections(text)
    if len(sections) <= 1:
        return _window_chunk(text, chunk_size=chunk_size, overlap=overlap)
    chunks: list[str] = []
    for section in sections:
        if not section.strip():
            continue
        if len(section) <= chunk_size:
            chunks.append(section.strip())
        else:
            chunks.extend(_window_chunk(section, chunk_size=chunk_size, overlap=overlap))
    return chunks


def _split_sections(text: str) -> list[str]:
    """Slice at markdown headings, keeping each heading with its section."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [text]
    sections: list[str] = []
    if matches[0].start() > 0:
        sections.append(text[: matches[0].start()])
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(text[match.start() : end])
    return sections


def _window_chunk(text: str, *, chunk_size: int, overlap: int) -> list[str]:
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
            tenant_id=doc.tenant_id or str(doc.metadata.get("tenant_id") or ""),
        )
        for i, piece in enumerate(pieces)
    ]
