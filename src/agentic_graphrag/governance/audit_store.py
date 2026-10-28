"""Persist reasoning chains for audit lookup by query_id (FR-AN-04 / P3-AN-01).

The JSONL file is size-rotated and the in-memory index is bounded: previously
the file grew without limit and was parsed whole on every service start (an
80 MB log after a few eval runs made start-up take seconds and held every chain
in memory).
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from agentic_graphrag.core.reasoning import ReasoningChain

DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_BACKUPS = 3
DEFAULT_MAX_INDEX = 5_000


class AuditStore:
    """JSONL + bounded in-memory index of reasoning chains."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_backups: int = DEFAULT_MAX_BACKUPS,
        max_index: int = DEFAULT_MAX_INDEX,
    ) -> None:
        self.path = Path(path) if path else None
        self.max_bytes = max_bytes
        self.max_backups = max_backups
        self.max_index = max_index
        self._index: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                qid = str(row.get("query_id") or "")
                if qid:
                    self._remember(qid, row)

    def _remember(self, qid: str, row: dict[str, Any]) -> None:
        self._index[qid] = row
        self._index.move_to_end(qid)
        while len(self._index) > self.max_index:
            self._index.popitem(last=False)

    def save(self, chain: ReasoningChain | dict[str, Any]) -> str:
        payload = (
            chain.model_dump(mode="json") if isinstance(chain, ReasoningChain) else dict(chain)
        )
        qid = str(payload.get("query_id") or "")
        if not qid:
            raise ValueError("chain missing query_id")
        with self._lock:
            self._remember(qid, payload)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                if self.path.stat().st_size > self.max_bytes:
                    self._rotate()
        return qid

    def _rotate(self) -> None:
        """``audit.jsonl`` → ``audit.jsonl.1`` …, keeping ``max_backups`` copies."""
        assert self.path is not None
        for i in range(self.max_backups, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            if not src.exists():
                continue
            if i >= self.max_backups:
                src.unlink()
            else:
                src.rename(self.path.with_name(f"{self.path.name}.{i + 1}"))
        if self.max_backups > 0:
            self.path.rename(self.path.with_name(f"{self.path.name}.1"))
        else:
            self.path.unlink()

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
