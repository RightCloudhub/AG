"""Document/chunk operation contracts shared by the doc store, workers and the API.

Commands (chunk edits, reparse, async parse) are committed as durable operation
records in the doc store in the same transaction as their intent, then dispatched
idempotently to the ingest task journal (K-04). Workers run them under a per-
document lease whose token fences every stage commit (release 5a).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class OpKind(StrEnum):
    CHUNK_PATCH = "chunk_patch"
    REPARSE = "reparse"
    PARSE = "parse"


class OpState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CONFLICT = "conflict"


ACTIVE_OP_STATES = (OpState.PENDING.value, OpState.RUNNING.value)


class DocumentOpsError(Exception):
    """Base class; ``status_code`` is the HTTP mapping used by the API."""

    status_code = 409
    code = "CONFLICT"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class RevisionConflict(DocumentOpsError):
    code = "REVISION_CONFLICT"


class IdempotencyConflict(DocumentOpsError):
    code = "IDEMPOTENCY_CONFLICT"


class DocumentBusy(DocumentOpsError):
    """Another worker holds the document lease, or a lifecycle intent is pending."""

    code = "DOCUMENT_BUSY"


class ReparseUnavailable(DocumentOpsError):
    code = "REPARSE_UNAVAILABLE"


class TargetNotFound(DocumentOpsError):
    status_code = 404
    code = "NOT_FOUND"


@dataclass(frozen=True)
class Fence:
    """Lease holder and fencing token (lease generation) for one document."""

    tenant_id: str
    doc_id: str
    owner: str
    token: int


@dataclass(frozen=True)
class CommandScope:
    """Idempotency identity: tenant + operation/target + client key."""

    tenant_id: str
    operation: str
    target: str
    key: str


@dataclass
class DocOperation:
    op_id: str
    tenant_id: str
    doc_id: str
    kind: str
    task_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    state: str = OpState.PENDING.value
    dispatched: bool = False
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def task_rows(self) -> list[dict[str, str]]:
        """Rows for the ingest task that executes this operation."""
        files = self.payload.get("files")
        if isinstance(files, list) and files:
            return [
                {
                    "doc_id": str(f.get("doc_id", "")),
                    "name": str(f.get("name", "")),
                    "status": "parsing",
                    "op_id": self.op_id,
                    "kind": self.kind,
                }
                for f in files
            ]
        row = {"doc_id": self.doc_id, "op_id": self.op_id, "kind": self.kind, "status": "queued"}
        if self.payload.get("chunk_id"):
            row["chunk_id"] = str(self.payload["chunk_id"])
        return [row]


@dataclass
class ChunkPatch:
    """Validated chunk edit/visibility intent."""

    tenant_id: str
    doc_id: str
    chunk_id: str
    expected_revision: int
    text: str | None = None
    enabled: bool | None = None
    token_count: int | None = None


@dataclass
class CommandOutcome:
    """Stored command response; ``replayed`` when an idempotent retry matched."""

    response: dict[str, Any]
    op: DocOperation | None = None
    replayed: bool = False


@dataclass(frozen=True)
class SourceArtifact:
    """Retained original upload bytes for one document version (opaque reference)."""

    sha256: str
    media_type: str = ""
    filename: str = ""
    size: int = 0


def payload_hash(payload: dict[str, Any]) -> str:
    """Canonical command payload hash (key order and whitespace independent)."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Command:
    """A command ready to commit: idempotency scope, payload digest, pending work."""

    scope: CommandScope
    digest: str
    op: DocOperation


@dataclass(frozen=True)
class DocumentQuery:
    tenant_id: str
    query: str = ""
    status: str = ""
    limit: int = 20
    offset: int = 0
