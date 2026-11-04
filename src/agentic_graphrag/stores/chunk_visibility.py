"""Authoritative chunk visibility backed by the doc store (C-04)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from agentic_graphrag.core.ports.stores import DocStore
from agentic_graphrag.core.ports.visibility import AllVisible, ChunkRef, ChunkVisibility
from agentic_graphrag.stores.doc_ops_sql import tenant_key

SQL_BATCH = 500
ChunkState = tuple[bool, int, int]
"""``(visible, doc_version, chunk_revision)`` of a managed reference."""


@runtime_checkable
class ChunkStateSource(Protocol):
    def tenant_generation(self, tenant_id: str) -> int: ...

    def chunk_states(self, tenant_id: str, refs: Sequence[ChunkRef]) -> dict[ChunkRef, ChunkState]:
        """States of managed references; refs to unmanaged documents are absent."""
        ...


@dataclass
class _Snapshot:
    docs: dict[str, tuple[int, str]] = field(default_factory=dict)
    chunks: dict[ChunkRef, tuple[int, int, int]] = field(default_factory=dict)
    dead: set[str] = field(default_factory=set)

    def state(self, ref: ChunkRef) -> ChunkState:
        doc = self.docs.get(ref[0])
        chunk = self.chunks.get(ref)
        if doc is None or chunk is None:
            return (False, doc[0] if doc else 0, 0)
        version, lifecycle = doc
        visible = not lifecycle and chunk[0] == version and bool(chunk[2])
        return (visible and ref[0] not in self.dead, version, chunk[1])


def _load_batch(db: sqlite3.Connection, tenant: str, batch: list[str], *, snap: _Snapshot) -> None:
    marks = ",".join("?" * len(batch))
    params = (tenant, *batch)
    rows = db.execute(
        f"SELECT id,version,lifecycle FROM documents WHERE tenant=? AND id IN ({marks})", params
    )
    snap.docs.update({r[0]: (int(r[1] or 0), r[2] or "") for r in rows})
    rows = db.execute(
        "SELECT doc_id,id,version,revision,enabled FROM chunks "
        f"WHERE tenant=? AND doc_id IN ({marks})",
        params,
    )
    snap.chunks.update({(r[0], r[1]): (int(r[2]), int(r[3]), int(r[4])) for r in rows})
    rows = db.execute(
        f"SELECT doc_id FROM tombstones WHERE tenant=? AND doc_id IN ({marks})", params
    )
    snap.dead.update(r[0] for r in rows)


def chunk_states(
    db: sqlite3.Connection, tenant: str, refs: Sequence[ChunkRef]
) -> dict[ChunkRef, ChunkState]:
    tenant = tenant_key(tenant)
    wanted = sorted({doc for doc, _ in refs if doc})
    snap = _Snapshot()
    for start in range(0, len(wanted), SQL_BATCH):
        _load_batch(db, tenant, wanted[start : start + SQL_BATCH], snap=snap)
    managed = snap.docs.keys() | snap.dead
    return {ref: snap.state(ref) for ref in refs if ref[0] in managed}


class DocStoreVisibility:
    """:class:`ChunkVisibility` over a store exposing SQLite chunk states."""

    def __init__(self, source: ChunkStateSource) -> None:
        self.source = source

    def generation(self, tenant_id: str) -> int:
        return self.source.tenant_generation(tenant_id)

    def visible(self, tenant_id: str, refs: Sequence[ChunkRef]) -> set[ChunkRef]:
        states = self.source.chunk_states(tenant_id, refs)
        return {ref for ref in refs if states.get(ref, (True, 0, 0))[0]}

    def current_revisions(
        self, tenant_id: str, refs: Sequence[ChunkRef]
    ) -> dict[ChunkRef, tuple[int, int]]:
        states = self.source.chunk_states(tenant_id, refs)
        return {ref: (s[1], s[2]) for ref, s in states.items() if s[0]}


def chunk_visibility_for(docs: DocStore) -> ChunkVisibility:
    """Visibility for a doc store; stores without lifecycle state serve everything."""
    if isinstance(docs, ChunkStateSource):
        return DocStoreVisibility(docs)
    return AllVisible()
