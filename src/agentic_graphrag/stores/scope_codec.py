"""Collision-safe ownership encoding for existing tenant-aware storage ports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Any, TypeVar

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.ports.stores import (
    ChunkRecord,
    DocumentRecord,
    EntityRecord,
    RelationRecord,
)
from agentic_graphrag.stores.record_copy import copy_record

Record = TypeVar("Record", EntityRecord, RelationRecord, DocumentRecord, ChunkRecord)
_NAMESPACE_PREFIX = "\x1fagkb:"
_GENERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def storage_tenant(scope: KnowledgeScope, *, generation: str = "1") -> str:
    """Keep legacy default storage readable; public tenants cannot contain controls."""
    if not _GENERATION.fullmatch(generation):
        raise ValueError("Invalid generation identifier")
    if scope.kb_id == "default" and generation == "1":
        return scope.tenant_id
    return _NAMESPACE_PREFIX + json.dumps(
        [scope.tenant_id, scope.kb_id, generation], ensure_ascii=True, separators=(",", ":")
    )


def parse_storage_tenant(owner: str) -> tuple[KnowledgeScope, str]:
    """Decode only persisted adapter owners, never use this to authenticate input."""
    if not owner.startswith(_NAMESPACE_PREFIX):
        return KnowledgeScope(tenant_id=owner or "default"), "1"
    values = json.loads(owner[len(_NAMESPACE_PREFIX) :])
    if (
        not isinstance(values, list)
        or len(values) != 3
        or not all(isinstance(value, str) for value in values)
    ):
        raise ValueError("Invalid persisted knowledge scope")
    tenant, kb, generation = values
    scope = KnowledgeScope(tenant_id=tenant, kb_id=kb)
    if storage_tenant(scope, generation=generation) != owner:
        raise ValueError("Knowledge scope encoding is not canonical")
    return scope, generation


@dataclass(frozen=True)
class ScopeBinding:
    scope: KnowledgeScope
    generation: str = "1"
    read_only: bool = False

    @cached_property
    def owner(self) -> str:
        return storage_tenant(self.scope, generation=self.generation)

    def check(self, tenant_id: str | None = None, *, write: bool = False) -> str:
        if write and self.read_only:
            raise ValueError("A sealed snapshot is read-only")
        if tenant_id is not None and (tenant_id or "default") != self.scope.tenant_id:
            raise ValueError("Tenant differs from the bound knowledge scope")
        return self.owner

    def encode(self, record: Record) -> Record:
        return self._encode(copy_record(record))

    def _encode(self, record: Record) -> Record:
        self.check(record.tenant_id or self.scope.tenant_id, write=True)
        if record.kb_id not in {"default", self.scope.kb_id}:
            raise ValueError("Record belongs to another knowledge base")
        record.tenant_id = self.owner
        record.kb_id = self.scope.kb_id
        record.generation = self.generation
        if isinstance(record, DocumentRecord):
            record.chunks = [self._encode(chunk) for chunk in record.chunks]
        if isinstance(record, (DocumentRecord, ChunkRecord)):
            record.metadata.update(
                tenant_id=self.owner, kb_id=self.scope.kb_id, generation=self.generation
            )
        return record

    def decode(self, record: Record, *, memo: dict[int, Any] | None = None) -> Record:
        if (record.tenant_id or "default") != self.owner:
            raise ValueError("Storage returned a record outside the bound knowledge scope")
        return self._decode(copy_record(record, memo=memo))

    def _decode(self, record: Record) -> Record:
        record.tenant_id = self.scope.tenant_id
        record.kb_id = self.scope.kb_id
        record.generation = self.generation
        if isinstance(record, DocumentRecord):
            for chunk in record.chunks:
                if (chunk.tenant_id or self.owner) != self.owner:
                    raise ValueError("Chunk belongs to another knowledge scope")
                self._decode(chunk)
        if isinstance(record, (DocumentRecord, ChunkRecord)):
            record.metadata.update(
                tenant_id=self.scope.tenant_id, kb_id=self.scope.kb_id, generation=self.generation
            )
        return record
