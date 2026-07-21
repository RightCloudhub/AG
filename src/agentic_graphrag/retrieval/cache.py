"""Retrieval / answer caches with index-version invalidation (P3-PERF-04).

| Cache              | Key                                      | Invalidation              |
|--------------------|------------------------------------------|---------------------------|
| embedding          | text hash                                | content-addressed (none)  |
| sub-query retrieval| norm(query) + index_version              | bump index_version        |
| hot answers        | norm(question) + index_version           | bump + optional TTL       |
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.retrieval.contracts import Candidate


def normalize_query_key(text: str) -> str:
    return " ".join((text or "").lower().split())


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
    value: Any
    created_at: float
    ttl_seconds: float | None = None

    def expired(self, now: float | None = None) -> bool:
        if self.ttl_seconds is None:
            return False
        t = now if now is not None else time.time()
        return (t - self.created_at) > self.ttl_seconds


class MemoryCache:
    """Thread-safe in-process cache with optional TTL."""

    def __init__(self, *, max_entries: int = 10_000) -> None:
        self.max_entries = max_entries
        self._data: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expired():
                del self._data[key]
                self.misses += 1
                return None
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any, *, ttl_seconds: float | None = None) -> None:
        with self._lock:
            if len(self._data) >= self.max_entries and key not in self._data:
                # Drop oldest
                oldest = min(self._data.items(), key=lambda kv: kv[1].created_at)
                del self._data[oldest[0]]
            self._data[key] = CacheEntry(
                value=value, created_at=time.time(), ttl_seconds=ttl_seconds
            )

    def invalidate_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                del self._data[k]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
            }


@dataclass
class IndexVersion:
    """Monotonic index version — bump after graph / vector / fulltext updates."""

    version: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def current(self) -> int:
        with self._lock:
            return self.version

    def bump(self) -> int:
        with self._lock:
            self.version += 1
            return self.version


class RetrievalCache:
    """Sub-query retrieval + answer cache keyed by index version."""

    def __init__(
        self,
        *,
        index_version: IndexVersion | None = None,
        answer_ttl_seconds: float = 3600.0,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.index_version = index_version or IndexVersion()
        self.answer_ttl_seconds = answer_ttl_seconds
        self.retrieval = MemoryCache()
        self.answers = MemoryCache()
        self.embeddings = MemoryCache(max_entries=50_000)
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def retrieval_key(self, query: str, tools: str = "", *, tenant_id: str = "") -> str:
        v = self.index_version.current()
        tenant = tenant_id or "default"
        body = f"{tenant}|{normalize_query_key(query)}|{tools}"
        return f"ret:v{v}:{content_hash(body)}"

    def answer_key(self, question: str, *, tenant_id: str = "") -> str:
        v = self.index_version.current()
        tenant = tenant_id or "default"
        body = f"{tenant}|{normalize_query_key(question)}"
        return f"ans:v{v}:{content_hash(body)}"

    def embedding_key(self, text: str) -> str:
        return f"emb:{content_hash(text)}"

    def get_retrieval(
        self, query: str, tools: str = "", *, tenant_id: str = ""
    ) -> list[Candidate] | None:
        raw = self.retrieval.get(self.retrieval_key(query, tools, tenant_id=tenant_id))
        if raw is None:
            return None
        return [Candidate.model_validate(c) for c in raw]

    def set_retrieval(
        self,
        query: str,
        candidates: list[Candidate],
        tools: str = "",
        *,
        tenant_id: str = "",
    ) -> None:
        payload = [c.model_dump(mode="json") for c in candidates]
        self.retrieval.set(
            self.retrieval_key(query, tools, tenant_id=tenant_id), payload
        )

    def get_answer(self, question: str, *, tenant_id: str = "") -> dict[str, Any] | None:
        return self.answers.get(self.answer_key(question, tenant_id=tenant_id))

    def set_answer(
        self, question: str, chain_payload: dict[str, Any], *, tenant_id: str = ""
    ) -> None:
        self.answers.set(
            self.answer_key(question, tenant_id=tenant_id),
            chain_payload,
            ttl_seconds=self.answer_ttl_seconds,
        )

    def get_embedding(self, text: str) -> list[float] | None:
        val = self.embeddings.get(self.embedding_key(text))
        return list(val) if isinstance(val, list) else None

    def set_embedding(self, text: str, vector: list[float]) -> None:
        self.embeddings.set(self.embedding_key(text), list(vector))

    def on_index_update(self) -> int:
        """Bump version so retrieval/answer keys naturally miss."""
        v = self.index_version.bump()
        # Optional: clear hot maps to free memory
        self.retrieval.clear()
        self.answers.clear()
        return v

    def persist_embeddings(self) -> None:
        if not self.cache_dir:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / "embeddings.json"
        # Only dump a small snapshot of keys (values may be large)
        stats = self.embeddings.stats()
        path.write_text(json.dumps(stats), encoding="utf-8")
