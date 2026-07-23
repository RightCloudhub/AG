"""Persist reasoning chains for audit lookup by query_id (FR-AN-04 / P3-AN-01)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from agentic_graphrag.generation.trace import ReasoningChain


class AuditStore:
    """JSONL + in-memory index of reasoning chains."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._index: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                qid = str(row.get("query_id") or "")
                if qid:
                    self._index[qid] = row
            except json.JSONDecodeError:
                continue

    def save(self, chain: ReasoningChain | dict[str, Any]) -> str:
        payload = (
            chain.model_dump(mode="json") if isinstance(chain, ReasoningChain) else dict(chain)
        )
        qid = str(payload.get("query_id") or "")
        if not qid:
            raise ValueError("chain missing query_id")
        with self._lock:
            self._index[qid] = payload
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return qid

    def get(self, query_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._index.get(query_id)
            return dict(row) if row else None

    def get_for_tenant(self, query_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Return chain only if it belongs to ``tenant_id`` (multi-tenant isolation)."""
        row = self.get(query_id)
        if row is None:
            return None
        meta = row.get("metadata") or {}
        owner = meta.get("tenant_id") if isinstance(meta, dict) else None
        # Legacy rows without tenant metadata are only visible to "default".
        if owner is None:
            return row if tenant_id == "default" else None
        return row if owner == tenant_id else None

    def list_ids(self, limit: int = 100) -> list[str]:
        with self._lock:
            return list(self._index.keys())[-limit:]
