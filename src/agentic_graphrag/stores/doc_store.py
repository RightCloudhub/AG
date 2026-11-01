"""Document store adapters implementing ``DocStore`` (P2-ARCH-02)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.core.tenancy import scoped_id, tenant_matches


class InMemoryDocStore:
    """Process-local document store for tests and offline API smoke."""

    durable = False

    def __init__(self) -> None:
        self._docs: dict[str, DocumentRecord] = {}

    def save(self, doc: DocumentRecord) -> None:
        self._docs[scoped_id(_tenant(doc), doc.doc_id)] = doc

    def get(self, doc_id: str, *, tenant_id: str | None = None) -> DocumentRecord | None:
        if tenant_id is not None:
            return self._docs.get(scoped_id(tenant_id, doc_id))
        matches = [doc for doc in self._docs.values() if doc.doc_id == doc_id]
        return matches[0] if len(matches) == 1 else None

    def list_ids(self, *, tenant_id: str | None = None) -> list[str]:
        return sorted(
            {doc.doc_id for doc in self._docs.values() if tenant_matches(_tenant(doc), tenant_id)}
        )


class FileDocStore:
    """JSON-per-document store; readable by any process sharing the directory."""

    durable = True

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, doc_id: str, tenant_id: str) -> Path:
        digest = hashlib.sha256(scoped_id(tenant_id, doc_id).encode()).hexdigest()
        return self.root / "v2" / f"{digest}.json"

    def save(self, doc: DocumentRecord) -> None:
        path = self._path(doc.doc_id, _tenant(doc))
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=path.parent) as directory:
            staging = Path(directory) / "document.json"
            staging.write_text(json.dumps(asdict(doc), ensure_ascii=False), encoding="utf-8")
            staging.replace(path)

    def get(self, doc_id: str, *, tenant_id: str | None = None) -> DocumentRecord | None:
        if tenant_id is not None:
            path = self._path(doc_id, tenant_id)
            if path.exists():
                doc = _read(path)
                return (
                    doc
                    if doc.doc_id == doc_id and tenant_matches(_tenant(doc), tenant_id)
                    else None
                )
        matches = {
            scoped_id(_tenant(doc), doc.doc_id): doc
            for doc in self._records(tenant_id)
            if doc.doc_id == doc_id
        }
        return next(iter(matches.values())) if len(matches) == 1 else None

    def list_ids(self, *, tenant_id: str | None = None) -> list[str]:
        return sorted({doc.doc_id for doc in self._records(tenant_id)})

    def _records(self, tenant_id: str | None) -> list[DocumentRecord]:
        paths = [*self.root.glob("*.json"), *(self.root / "v2").glob("*.json")]
        return [doc for path in paths if tenant_matches(_tenant(doc := _read(path)), tenant_id)]


def _read(path: Path) -> DocumentRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    return DocumentRecord(
        doc_id=data["doc_id"],
        title=data.get("title", ""),
        content=data.get("content", ""),
        metadata=data.get("metadata") or {},
        tenant_id=str(data.get("tenant_id") or (data.get("metadata") or {}).get("tenant_id", "")),
    )


def _tenant(doc: DocumentRecord) -> str:
    return doc.tenant_id or str(doc.metadata.get("tenant_id") or "")
