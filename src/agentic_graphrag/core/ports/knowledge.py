"""Knowledge metadata and immutable generation payload ports."""

import builtins
from typing import Protocol

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope, SummaryRecord
from agentic_graphrag.core.knowledge_versions import GraphChangeSet, KnowledgeSnapshot, WriteIntent


class KnowledgeStore(Protocol):
    def get(self, scope: KnowledgeScope) -> KnowledgeBase | None: ...

    def list(self, tenant_id: str, *, limit: int = 100, offset: int = 0) -> list[KnowledgeBase]: ...

    def scopes(self) -> builtins.list[KnowledgeScope]: ...

    def create(self, kb: KnowledgeBase) -> KnowledgeBase: ...

    def ensure_default(self, tenant_id: str) -> KnowledgeBase: ...

    def update(self, kb: KnowledgeBase, *, expected_revision: int) -> KnowledgeBase: ...

    def begin_write(self, intent: WriteIntent) -> WriteIntent: ...

    def get_write(self, scope: KnowledgeScope, operation_id: str) -> WriteIntent | None: ...

    def mark_seeded(
        self,
        scope: KnowledgeScope,
        operation_id: str,
        *,
        index_generations: tuple[tuple[str, str], ...] = (),
    ) -> WriteIntent: ...

    def set_write_documents(
        self, scope: KnowledgeScope, operation_id: str, *, document_ids: tuple[str, ...]
    ) -> WriteIntent: ...

    def pending_writes(self, scope: KnowledgeScope) -> builtins.list[WriteIntent]: ...

    def complete_write(self, change: GraphChangeSet) -> GraphChangeSet: ...

    def changes(
        self, scope: KnowledgeScope, *, after_sequence: int = 0, limit: int = 100
    ) -> builtins.list[GraphChangeSet]: ...

    def save_snapshot(self, snapshot: KnowledgeSnapshot, payload: str) -> KnowledgeSnapshot: ...

    def get_snapshot(
        self, scope: KnowledgeScope, generation: str
    ) -> tuple[KnowledgeSnapshot, str] | None: ...

    def publish(
        self, scope: KnowledgeScope, *, generation: str, expected_revision: int
    ) -> KnowledgeBase: ...

    def commit_generation(
        self,
        change: GraphChangeSet,
        *,
        expected_revision: int,
        target_config: KnowledgeBase | None = None,
    ) -> KnowledgeBase: ...

    def tombstone(self, scope: KnowledgeScope, doc_id: str, *, through_version: int) -> None: ...

    def is_tombstoned(self, scope: KnowledgeScope, doc_id: str, *, version: int) -> bool: ...

    def close(self) -> None: ...


class SummaryStore(Protocol):
    def save_summary(self, summary: SummaryRecord) -> None: ...

    def summaries(self, scope: KnowledgeScope, *, generation: str) -> list[SummaryRecord]: ...
