"""Filesystem-backed document store for POC."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_graphrag.stores.interfaces import DocumentRecord


class FileDocStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, doc_id: str) -> Path:
        safe = doc_id.replace("/", "_")
        return self.root / f"{safe}.json"

    def save(self, doc: DocumentRecord) -> None:
        path = self._path(doc.doc_id)
        path.write_text(
            json.dumps(
                {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "content": doc.content,
                    "metadata": doc.metadata,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def get(self, doc_id: str) -> DocumentRecord | None:
        path = self._path(doc_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return DocumentRecord(
            doc_id=data["doc_id"],
            title=data.get("title", ""),
            content=data.get("content", ""),
            metadata=data.get("metadata") or {},
        )

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))
