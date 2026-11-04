"""Doc-store capability used by document commands, workers and read models (K-01…K-06).

Implemented by ``FileDocStore``; process-local stores do not offer it, and the API
reports the related capabilities as unavailable.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol, runtime_checkable

from agentic_graphrag.core.ports.document_ops import (
    ChunkPatch,
    Command,
    CommandOutcome,
    DocOperation,
    DocumentQuery,
    Fence,
    SourceArtifact,
)
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord


@runtime_checkable
class LeaseStore(Protocol):
    def acquire_lease(self, ref: tuple[str, str], *, owner: str, ttl: float) -> Fence | None: ...

    def renew_lease(self, fence: Fence, *, ttl: float) -> bool: ...

    def release_lease(self, fence: Fence) -> None: ...

    def fence_valid(self, fence: Fence) -> bool: ...

    def bump_generation(self, tenant_id: str) -> int: ...


@runtime_checkable
class BlobStore(Protocol):
    def put(self, tenant_id: str, data: bytes) -> str: ...

    def read(self, tenant_id: str, sha256: str) -> bytes: ...

    def exists(self, tenant_id: str, sha256: str) -> bool: ...


@runtime_checkable
class DocumentOpsStore(LeaseStore, Protocol):
    @property
    def artifacts(self) -> BlobStore: ...

    def lock(self) -> AbstractContextManager[None]: ...

    def get(self, doc_id: str, *, tenant_id: str | None = None) -> DocumentRecord | None: ...

    def find(
        self, value: str, *, tenant_id: str, by_hash: bool = False
    ) -> DocumentRecord | None: ...

    def tenant_generation(self, tenant_id: str) -> int: ...

    def apply_chunk_patch(
        self,
        patch: ChunkPatch,
        command: Command,
        respond: Callable[[ChunkRecord, DocOperation], dict[str, Any]],
    ) -> CommandOutcome: ...

    def record_command(
        self, command: Command, respond: Callable[[DocOperation], dict[str, Any]]
    ) -> CommandOutcome: ...

    def stage_parse(self, op: DocOperation, reservations: list[dict[str, str]]) -> None: ...

    def get_op(self, op_id: str) -> DocOperation | None: ...

    def undispatched_ops(self, limit: int) -> list[DocOperation]: ...

    def doc_ops(self, tenant_id: str, doc_ids: list[str]) -> list[DocOperation]: ...

    def update_op(self, op_id: str, **fields: Any) -> None: ...

    def set_reservations(self, op_id: str, *, state: str, error: str = "") -> None: ...

    def mutation_seq(self, tenant_id: str, doc_id: str) -> int: ...

    def commit_chunk_stage(
        self, chunk: ChunkRecord, *, fence: Fence, doc: DocumentRecord
    ) -> bool: ...

    def record_source(self, ref: tuple[str, str, int], artifact: SourceArtifact) -> None: ...

    def source_for(self, ref: tuple[str, str, int]) -> SourceArtifact | None: ...

    def stage_content(
        self, op_id: str, ref: tuple[str, str], parsed: tuple[str, dict[str, Any]]
    ) -> None: ...

    def staged_content(self, op_id: str) -> tuple[str, dict[str, Any]] | None: ...

    def list_documents(self, query: DocumentQuery) -> tuple[list[dict[str, Any]], int]: ...

    def document_row(self, tenant_id: str, doc_id: str) -> dict[str, Any] | None: ...

    def list_chunk_rows(
        self, ref: tuple[str, str], page: tuple[int, int]
    ) -> tuple[list[dict[str, Any]], int]: ...

    def chunk_detail_row(self, ref: tuple[str, str, str]) -> tuple[dict[str, Any], str] | None: ...
