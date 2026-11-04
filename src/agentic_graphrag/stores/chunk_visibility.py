"""Authoritative chunk visibility backed by the doc store (C-04)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
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


def chunk_states(
    db: sqlite3.Connection, tenant: str, refs: Sequence[ChunkRef]
) -> dict[ChunkRef, ChunkState]:
    tenant = tenant_key(tenant)
    wanted = sorted({doc for doc, _ in refs if doc})
    docs: dict[str, tuple[int, str]] = {}
    chunks: dict[ChunkRef, tuple[int, int, int]] = {}
    dead: set[str] = set()
    for start in range(0, len(wanted), SQL_BATCH):
        batch = wanted[start : start + SQL_BATCH]
        marks = ",".join("?" * len(batch))
        for did, version, lifecycle in db.execute(
            f"SELECT id,version,lifecycle FROM documents WHERE tenant=? AND id IN ({marks})",
            (tenant, *batch),
        ):
            docs[did] = (int(version or 0), lifecycle or "")
        for did, cid, version, revision, enabled in db.execute(
            "SELECT doc_id,id,version,revision,enabled FROM chunks "
            f"WHERE tenant=? AND doc_id IN ({marks})",
            (tenant, *batch),
        ):
            chunks[(did, cid)] = (int(version), int(revision), int(enabled))
        dead.update(
            r[0]
            for r in db.execute(
                f"SELECT doc_id FROM tombstones WHERE tenant=? AND doc_id IN ({marks})",
                (tenant, *batch),
            )
        )
    return {ref: _state(ref, docs, chunks, dead) for ref in refs if ref[0] in docs or ref[0] in dead}


def _state(
    ref: ChunkRef,
    docs: dict[str, tuple[int, str]],
    chunks: dict[ChunkRef, tuple[int, int, int]],
    dead: set[str],
) -> ChunkState:
    doc = docs.get(ref[0])
    chunk = chunks.get(ref)
    if doc is None or chunk is None:
        return (False, doc[0] if doc else 0, 0)
    version, lifecycle = doc
    visible = not lifecycle and chunk[0] == version and bool(chunk[2]) and ref[0] not in dead
    return (visible, version, chunk[1])


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
