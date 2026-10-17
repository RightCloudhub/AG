"""BM25 fulltext store shared across processes through one snapshot file (O-3).

The API process and the standalone ingest worker each hold a
:class:`BM25FulltextStore` in memory. Before V2 the worker's writes never left
its process, so the API silently ran with two retrieval channels. This store:

* writes the snapshot atomically (temp file + ``os.replace``) after ``index()``;
* reloads it before ``search()`` whenever the file's ``mtime_ns`` changed.

It is correct for processes sharing one volume. Multi-host deployments need a
server-side store (ARCH_OPTIMIZATION_V2 S-2).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore

logger = logging.getLogger(__name__)


class SharedFileBM25Store:
    """FulltextStore whose state is the snapshot file at ``path``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._inner = BM25FulltextStore()
        self._lock = threading.RLock()
        self._seen_mtime: int | None = None
        self.reloads = 0
        self._refresh()

    def _mtime(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _refresh(self) -> None:
        mtime = self._mtime()
        if mtime is None or mtime == self._seen_mtime:
            return
        try:
            self._inner.load(str(self.path))
        except (OSError, ValueError, KeyError) as exc:
            # A torn read cannot happen (atomic replace); a corrupt file must not
            # take search down — keep the last good state and retry next call.
            logger.warning("fulltext snapshot reload failed (%s): %s", self.path, exc)
            return
        self._seen_mtime = mtime
        self.reloads += 1

    def _persist(self) -> None:
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        self._inner.save(str(tmp))
        os.replace(tmp, self.path)
        self._seen_mtime = self._mtime()

    def index(self, chunks: list[ChunkRecord]) -> int:
        with self._lock:
            self._refresh()  # merge onto the newest snapshot, not a stale copy
            n = self._inner.index(chunks)
            self._persist()
            return n

    def search(
        self, query: str, top_k: int = 10, *, tenant_id: str | None = None
    ) -> list[tuple[ChunkRecord, float]]:
        with self._lock:
            self._refresh()
            return self._inner.search(query, top_k, tenant_id=tenant_id)

    def clear(self) -> None:
        with self._lock:
            self._inner.clear()
            self._persist()

    def save(self, path: str) -> None:
        with self._lock:
            self._inner.save(path)

    def load(self, path: str) -> int:
        with self._lock:
            n = self._inner.load(path)
            self._persist()
            return n
