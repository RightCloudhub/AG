"""Operation, lease, generation and read-model methods mixed into FileDocStore."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from agentic_graphrag.core.ports.document_ops import (
    ChunkPatch,
    CommandOutcome,
    DocOperation,
    Fence,
    SourceArtifact,
)
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.core.ports.visibility import ChunkRef
from agentic_graphrag.stores import doc_artifacts_sql as art
from agentic_graphrag.stores import doc_commands_sql as cmd
from agentic_graphrag.stores import doc_ops_sql as ops
from agentic_graphrag.stores import doc_projection as proj
from agentic_graphrag.stores.chunk_visibility import ChunkState, chunk_states
from agentic_graphrag.stores.source_artifacts import SourceArtifactStore


class DocOpsMixin:
    root: Path

    def _connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    def _read(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with closing(self._connect()) as db:
            return fn(db)

    def _write(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with closing(self._connect()) as db, db:
            return fn(db)

    @property
    def artifacts(self) -> SourceArtifactStore:
        return SourceArtifactStore(self.root / "sources")

    def tenant_generation(self, tenant_id: str) -> int:
        return int(self._read(lambda db: ops.generation(db, tenant_id)))

    def bump_generation(self, tenant_id: str) -> int:
        return int(self._write(lambda db: ops.bump_generation(db, tenant_id)))

    def chunk_states(self, tenant_id: str, refs: Sequence[ChunkRef]) -> dict[ChunkRef, ChunkState]:
        if not refs:
            return {}
        result: dict[ChunkRef, ChunkState] = self._read(
            lambda db: chunk_states(db, tenant_id, refs)
        )
        return result

    def apply_chunk_patch(
        self, patch: ChunkPatch, command: cmd.Command, respond: cmd.ChunkResponder
    ) -> CommandOutcome:
        outcome: CommandOutcome = self._write(
            lambda db: cmd.apply_chunk_patch(db, patch, command, respond)
        )
        return outcome

    def record_command(
        self, command: cmd.Command, respond: Callable[[DocOperation], dict[str, Any]]
    ) -> CommandOutcome:
        outcome: CommandOutcome = self._write(lambda db: cmd.record_command(db, command, respond))
        return outcome

    def stage_parse(self, op: DocOperation, reservations: list[dict[str, str]]) -> None:
        """Durably stage an async parse: operation, reserved identities, generation."""

        def run(db: sqlite3.Connection) -> None:
            cmd.begin_immediate(db)
            if ops.get_op(db, op.op_id) is not None:
                return
            ops.insert_op(db, op)
            for row in reservations:
                art.reserve(db, op.op_id, (op.tenant_id, row["doc_id"]), row)
            ops.bump_generation(db, op.tenant_id)

        self._write(run)

    def get_op(self, op_id: str) -> DocOperation | None:
        result: DocOperation | None = self._read(lambda db: ops.get_op(db, op_id))
        return result

    def undispatched_ops(self, limit: int) -> list[DocOperation]:
        result: list[DocOperation] = self._read(lambda db: ops.undispatched_ops(db, limit))
        return result

    def doc_ops(self, tenant_id: str, doc_ids: list[str]) -> list[DocOperation]:
        if not doc_ids:
            return []
        result: list[DocOperation] = self._read(lambda db: ops.active_ops(db, tenant_id, doc_ids))
        return result

    def update_op(self, op_id: str, **fields: Any) -> None:
        self._write(lambda db: ops.update_op(db, op_id, **fields))

    def set_reservations(self, op_id: str, *, state: str, error: str = "") -> None:
        self._write(lambda db: art.set_reservations(db, op_id, state=state, error=error))

    def acquire_lease(self, ref: tuple[str, str], *, owner: str, ttl: float) -> Fence | None:
        result: Fence | None = self._write(
            lambda db: ops.acquire_lease(db, ref, owner=owner, ttl=ttl)
        )
        return result

    def renew_lease(self, fence: Fence, *, ttl: float) -> bool:
        return bool(self._write(lambda db: ops.renew_lease(db, fence, ttl=ttl)))

    def release_lease(self, fence: Fence) -> None:
        self._write(lambda db: ops.release_lease(db, fence))

    def fence_valid(self, fence: Fence) -> bool:
        return bool(self._read(lambda db: ops.fence_valid(db, fence)))

    def mutation_seq(self, tenant_id: str, doc_id: str) -> int:
        return int(self._read(lambda db: ops.mutation_seq(db, tenant_id, doc_id)))

    def commit_chunk_stage(self, chunk: ChunkRecord, *, fence: Fence, doc: DocumentRecord) -> bool:
        return bool(self._write(lambda db: cmd.commit_stage(db, chunk, fence=fence, doc=doc)))

    def record_source(self, ref: tuple[str, str, int], artifact: SourceArtifact) -> None:
        self._write(lambda db: art.record_source(db, ref, artifact))

    def source_for(self, ref: tuple[str, str, int]) -> SourceArtifact | None:
        result: SourceArtifact | None = self._read(lambda db: art.source_for(db, ref))
        return result

    def stage_content(
        self, op_id: str, ref: tuple[str, str], parsed: tuple[str, dict[str, Any]]
    ) -> None:
        self._write(lambda db: art.stage_content(db, op_id, ref, parsed))

    def staged_content(self, op_id: str) -> tuple[str, dict[str, Any]] | None:
        result: tuple[str, dict[str, Any]] | None = self._read(
            lambda db: art.staged_content(db, op_id)
        )
        return result

    def list_documents(self, query: proj.DocumentQuery) -> tuple[list[dict[str, Any]], int]:
        result: tuple[list[dict[str, Any]], int] = self._read(
            lambda db: proj.list_documents(db, query)
        )
        return result

    def document_row(self, tenant_id: str, doc_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = self._read(
            lambda db: proj.document_row(db, tenant_id, doc_id)
        )
        return result

    def list_chunk_rows(
        self, ref: tuple[str, str], page: tuple[int, int]
    ) -> tuple[list[dict[str, Any]], int]:
        result: tuple[list[dict[str, Any]], int] = self._read(
            lambda db: proj.list_chunk_rows(db, ref, page)
        )
        return result

    def chunk_detail_row(self, ref: tuple[str, str, str]) -> tuple[dict[str, Any], str] | None:
        result: tuple[dict[str, Any], str] | None = self._read(
            lambda db: proj.chunk_detail_row(db, ref)
        )
        return result
