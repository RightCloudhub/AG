"""Authoritative chunk visibility and shared index generations (C-04).

The doc store is the source of truth for which chunks may be served: a chunk is
invisible when it is disabled, no longer part of its document's current version,
or its document has a pending delete/replace intent or was deleted. References to
documents the doc store has never managed (seed/demo corpora) stay visible.

``generation(tenant_id)`` is a monotonic, persisted per-tenant counter bumped on
mutation intent and stage completion; caches in separate API/worker processes
compare it instead of relying on the process-local ``IndexVersion``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from agentic_graphrag.core.evidence import Candidate

ChunkRef = tuple[str, str]
"""``(doc_id, chunk_id)``; ``doc_id`` may be empty for legacy graph provenance."""

CANDIDATE_DOC_VERSION_KEY = "doc_version"
CANDIDATE_REVISION_KEY = "chunk_revision"


@runtime_checkable
class ChunkVisibility(Protocol):
    def generation(self, tenant_id: str) -> int:
        """Current persisted index generation of ``tenant_id`` (0 when never bumped)."""
        ...

    def visible(self, tenant_id: str, refs: Sequence[ChunkRef]) -> set[ChunkRef]:
        """The subset of ``refs`` that may be served now."""
        ...

    def current_revisions(
        self, tenant_id: str, refs: Sequence[ChunkRef]
    ) -> dict[ChunkRef, tuple[int, int]]:
        """``(doc_version, chunk_revision)`` of the managed, visible refs among ``refs``."""
        ...


class AllVisible:
    """Visibility for stores without lifecycle state: everything is served."""

    def generation(self, tenant_id: str) -> int:
        return 0

    def visible(self, tenant_id: str, refs: Sequence[ChunkRef]) -> set[ChunkRef]:
        return set(refs)

    def current_revisions(
        self, tenant_id: str, refs: Sequence[ChunkRef]
    ) -> dict[ChunkRef, tuple[int, int]]:
        return {}


def candidate_refs(candidate: Candidate) -> list[ChunkRef]:
    """Chunk references cited by ``candidate`` (graph provenance included)."""
    return [(c.doc_id or "", c.chunk_id) for c in candidate.citations if c.chunk_id]


def filter_visible_candidates(
    candidates: Iterable[Candidate], visibility: ChunkVisibility, tenant_id: str
) -> list[Candidate]:
    """Drop candidates whose every cited chunk is invisible (pure; inputs unchanged).

    Candidates without chunk citations (e.g. seed graph facts) are kept. A kept
    candidate loses its invisible chunk citations. When a single-chunk candidate
    carries ``metadata.doc_version`` / ``metadata.chunk_revision`` and they differ
    from the current revision, it is obsolete and dropped as well.
    """
    pool = list(candidates)
    refs = sorted({ref for c in pool for ref in candidate_refs(c)})
    if not refs:
        return pool
    shown = visibility.visible(tenant_id, refs)
    revisions = visibility.current_revisions(tenant_id, refs)
    out: list[Candidate] = []
    for candidate in pool:
        kept = _visible_copy(candidate, shown, revisions)
        if kept is not None:
            out.append(kept)
    return out


def _visible_copy(
    candidate: Candidate, shown: set[ChunkRef], revisions: dict[ChunkRef, tuple[int, int]]
) -> Candidate | None:
    refs = candidate_refs(candidate)
    if not refs:
        return candidate
    if not any(ref in shown for ref in refs) or _stale(candidate, refs, revisions):
        return None
    if all(ref in shown for ref in refs):
        return candidate
    citations = [
        c for c in candidate.citations if not c.chunk_id or (c.doc_id or "", c.chunk_id) in shown
    ]
    return candidate.model_copy(update={"citations": citations})


def _stale(
    candidate: Candidate, refs: list[ChunkRef], revisions: dict[ChunkRef, tuple[int, int]]
) -> bool:
    meta = candidate.metadata or {}
    if len(set(refs)) != 1 or CANDIDATE_REVISION_KEY not in meta:
        return False
    current = revisions.get(refs[0])
    if current is None:
        return False
    version = int(meta.get(CANDIDATE_DOC_VERSION_KEY) or current[0])
    return (version, int(meta[CANDIDATE_REVISION_KEY])) != current
