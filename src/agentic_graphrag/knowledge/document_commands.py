"""Durable document/chunk commands: commit intent, then dispatch work idempotently (K-04).

The doc-store transaction records the revision/visibility intent, the tenant
generation bump, the idempotency result and a pending operation together. Only
then is the operation copied into the JSONL ingest task journal; that copy is
idempotent (the task ID is reserved in the operation) and every API call and
worker poll retries undispatched operations, so a crash between the two writes
still yields exactly one task.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.config_knowledge import DEFAULT_CHUNK_TOKENS, DEFAULT_TOKENIZER
from agentic_graphrag.core.ports.document_ops import (
    ChunkPatch,
    Command,
    CommandOutcome,
    CommandScope,
    DocOperation,
    OpKind,
    ReparseUnavailable,
    TargetNotFound,
    payload_hash,
)
from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.knowledge.tokenizer import get_tokenizer
from agentic_graphrag.observability.degradation import REASON_UNEXPECTED, record_degradation

DISPATCH_BATCH = 100
REPARSE_MODES = ("source", "rechunk")


class InvalidCommand(ValueError):
    """Rejected before any durable write (mapped to 422)."""


@dataclass(frozen=True)
class StagedFile:
    name: str
    data: bytes
    source_uri: str
    media_type: str = ""


class DocumentCommands:
    def __init__(
        self,
        docs: DocumentOpsStore,
        tasks: IngestTaskStore | None,
        *,
        tokenizer: str = DEFAULT_TOKENIZER,
        max_tokens: int = DEFAULT_CHUNK_TOKENS,
    ) -> None:
        self.docs, self.tasks = docs, tasks
        self.tokenizer, self.max_tokens = tokenizer, max_tokens

    def patch_chunk(
        self,
        patch: ChunkPatch,
        *,
        key: str,
        respond: Callable[[ChunkRecord, DocOperation], dict[str, Any]],
    ) -> CommandOutcome:
        payload = {
            "text": patch.text,
            "enabled": patch.enabled,
            "expected_revision": patch.expected_revision,
        }
        if patch.text is not None:
            patch.token_count = self._checked_tokens(patch.text)
        op = self._op(patch.tenant_id, patch.doc_id, OpKind.CHUNK_PATCH)
        op.payload.update(chunk_id=patch.chunk_id, revision=patch.expected_revision + 1)
        scope = CommandScope(patch.tenant_id, OpKind.CHUNK_PATCH, _target(patch), key)
        outcome = self.docs.apply_chunk_patch(
            patch, Command(scope, payload_hash(payload), op), respond
        )
        self.dispatch_pending()
        return outcome

    def request_reparse(
        self,
        ref: tuple[str, str],
        *,
        expected_version: int,
        mode: str,
        key: str,
        respond: Callable[[DocOperation], dict[str, Any]],
    ) -> CommandOutcome:
        tenant, doc_id = ref
        if mode not in REPARSE_MODES:
            raise InvalidCommand(f"mode must be one of {', '.join(REPARSE_MODES)}")
        op = self._op(tenant, doc_id, OpKind.REPARSE)
        op.payload.update(expected_version=expected_version, mode=mode)
        if mode == "source":
            op.payload["sha256"] = self._retained_source(tenant, doc_id, expected_version)
        scope = CommandScope(tenant, OpKind.REPARSE, doc_id, key)
        digest = payload_hash({"expected_version": expected_version, "mode": mode})
        outcome = self.docs.record_command(Command(scope, digest, op), respond)
        self.dispatch_pending()
        return outcome

    def _retained_source(self, tenant: str, doc_id: str, version: int) -> str:
        if self.docs.get(doc_id, tenant_id=tenant) is None:
            raise TargetNotFound("Unknown document")
        source = self.docs.source_for((tenant, doc_id, version))
        if source is None or not self.docs.artifacts.exists(tenant, source.sha256):
            raise ReparseUnavailable(
                "No retained original source for this version; re-upload the file or "
                "use mode=rechunk to rebuild from the stored text",
                details={"rechunk_available": True},
            )
        return source.sha256

    def stage_async_parse(
        self, tenant: str, files: list[StagedFile], *, task_id: str
    ) -> DocOperation:
        """Durably stage originals and reserve identities; parsing happens in a worker."""
        rows = [self._staged_row(tenant, f) for f in files]
        op = DocOperation(task_id, tenant, rows[0]["doc_id"], OpKind.PARSE, task_id)
        op.payload["files"] = rows
        reservations = [
            {"doc_id": r["doc_id"], "title": r["name"], "source": r["source_uri"]} for r in rows
        ]
        self.docs.stage_parse(op, reservations)
        self.dispatch_pending()
        return self.docs.get_op(task_id) or op

    def _staged_row(self, tenant: str, file: StagedFile) -> dict[str, Any]:
        sha256 = self.docs.artifacts.put(tenant, file.data)
        existing = self.docs.find(file.source_uri, tenant_id=tenant)
        return {
            "doc_id": existing.doc_id if existing else str(uuid.uuid4()),
            "name": file.name,
            "source_uri": file.source_uri,
            "sha256": sha256,
            "size": len(file.data),
            "media_type": file.media_type,
        }

    def dispatch_pending(self, limit: int = DISPATCH_BATCH) -> int:
        """Copy undispatched operations into the task journal; safe to repeat."""
        if self.tasks is None:
            return 0
        dispatched = 0
        for op in self.docs.undispatched_ops(limit):
            try:
                self.tasks.ensure(op.tenant_id, op.task_rows(), task_id=op.task_id)
                self.docs.update_op(op.op_id, dispatched=1)
            except Exception as exc:  # noqa: BLE001 — reconciled on the next poll
                record_degradation("document_ops", REASON_UNEXPECTED, exc, op_id=op.op_id)
                break
            dispatched += 1
        return dispatched

    def _checked_tokens(self, text: str) -> int:
        if not text.strip():
            raise InvalidCommand("Chunk text must not be empty")
        count = get_tokenizer(self.tokenizer).count(text)
        if count > self.max_tokens:
            raise InvalidCommand(f"Chunk text exceeds {self.max_tokens} tokens")
        return count

    @staticmethod
    def _op(tenant: str, doc_id: str, kind: OpKind) -> DocOperation:
        return DocOperation(str(uuid.uuid4()), tenant, doc_id, kind.value, str(uuid.uuid4()))


def _target(patch: ChunkPatch) -> str:
    return f"{patch.doc_id}/{patch.chunk_id}"
