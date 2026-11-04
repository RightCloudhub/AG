"""Retention for document-operation records and retained sources (K-06, ENT-06).

Never removes anything still needed: sources of a current document version or of
a not-yet-pruned operation, the current chunk revision's history, and the
idempotency records of operations that are still pending/running (those keys
never expire). Expired, unreferenced source blobs (e.g. from an upload that
crashed before linking) are collected after the source retention period.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Protocol

from agentic_graphrag.core.ports.document_ops import ACTIVE_OP_STATES
from agentic_graphrag.stores.source_artifacts import SourceArtifactStore, tenant_dir_name

SECONDS_PER_DAY = 86_400.0
_TERMINAL = "('done','failed','conflict')"
_ACTIVE = "(" + ",".join(f"'{s}'" for s in ACTIVE_OP_STATES) + ")"


class RetentionPeriods(Protocol):
    source_artifacts_days: int
    staged_operations_days: int
    historical_revisions_days: int
    idempotency_days: int


class PrunableDocStore(Protocol):
    @property
    def artifacts(self) -> SourceArtifactStore: ...

    def _connect(self) -> sqlite3.Connection: ...


@dataclass(frozen=True)
class Cutoffs:
    sources: float
    staged: float
    history: float
    idempotency: float

    @classmethod
    def of(cls, retention: RetentionPeriods, *, now: float) -> Cutoffs:
        return cls(
            now - retention.source_artifacts_days * SECONDS_PER_DAY,
            now - retention.staged_operations_days * SECONDS_PER_DAY,
            now - retention.historical_revisions_days * SECONDS_PER_DAY,
            now - retention.idempotency_days * SECONDS_PER_DAY,
        )


_STATEMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "staged",
        "staged",
        f"DELETE FROM staged WHERE op_id IN (SELECT id FROM ops WHERE state IN {_TERMINAL} "
        "AND updated_at < :staged)",
    ),
    (
        "reservations",
        "staged",
        "DELETE FROM reservations WHERE state IN ('published','failed') AND updated_at < :staged",
    ),
    (
        "operations",
        "staged",
        f"DELETE FROM ops WHERE state IN {_TERMINAL} AND updated_at < :staged",
    ),
    ("tombstones", "staged", "DELETE FROM tombstones WHERE deleted_at < :staged"),
    (
        "idempotency",
        "idempotency",
        "DELETE FROM idempotency WHERE created_at < :idempotency AND NOT EXISTS("
        f"SELECT 1 FROM ops o WHERE o.id=idempotency.op_id AND o.state IN {_ACTIVE})",
    ),
    (
        "chunk_history",
        "history",
        "DELETE FROM chunk_history WHERE created_at < :history AND NOT EXISTS("
        "SELECT 1 FROM chunks c WHERE c.tenant=chunk_history.tenant AND "
        "c.doc_id=chunk_history.doc_id AND c.id=chunk_history.chunk_id AND "
        "c.version=chunk_history.version AND c.revision=chunk_history.revision)",
    ),
    (
        "source_records",
        "sources",
        "DELETE FROM sources WHERE created_at < :sources AND NOT EXISTS("
        "SELECT 1 FROM documents d WHERE d.tenant=sources.tenant AND d.id=sources.doc_id "
        "AND d.version=sources.version)",
    ),
)


def _op_shas(payload: dict[str, Any]) -> Iterator[str]:
    if payload.get("sha256"):
        yield str(payload["sha256"])
    for file in payload.get("files") or []:
        if isinstance(file, dict) and file.get("sha256"):
            yield str(file["sha256"])


def _referenced(db: sqlite3.Connection) -> set[tuple[str, str]]:
    refs = {(tenant_dir_name(t), sha) for t, sha in db.execute("SELECT tenant,sha256 FROM sources")}
    for tenant, payload in db.execute("SELECT tenant,payload FROM ops"):
        refs.update((tenant_dir_name(tenant), sha) for sha in _op_shas(json.loads(payload or "{}")))
    return refs


def prune_doc_store(
    store: PrunableDocStore, retention: RetentionPeriods, *, now: float, dry_run: bool = False
) -> dict[str, int]:
    """Apply retention; with ``dry_run`` report counts and change nothing."""
    cutoffs = Cutoffs.of(retention, now=now)
    params = {
        "sources": cutoffs.sources,
        "staged": cutoffs.staged,
        "history": cutoffs.history,
        "idempotency": cutoffs.idempotency,
    }
    counts: dict[str, int] = {}
    with closing(store._connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        for name, _period, sql in _STATEMENTS:
            counts[name] = db.execute(sql, params).rowcount
        referenced = _referenced(db)
        if dry_run:
            db.rollback()
        else:
            db.commit()
    expired = [
        blob
        for blob in store.artifacts.blobs()
        if (blob.tenant_dir, blob.sha256) not in referenced and blob.mtime < cutoffs.sources
    ]
    if not dry_run:
        for blob in expired:
            blob.path.unlink(missing_ok=True)
    counts["source_blobs"] = len(expired)
    return counts
