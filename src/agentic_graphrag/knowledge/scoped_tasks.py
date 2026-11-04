"""Knowledge-scoped view of the existing recoverable ingest task journal."""

from __future__ import annotations

import builtins
from dataclasses import replace

from agentic_graphrag.core.constants import DEFAULT_PAGE_SIZE
from agentic_graphrag.knowledge.ingest_tasks import (
    DEFAULT_STALE_SECONDS,
    IngestStatus,
    IngestTask,
    IngestTaskStore,
)
from agentic_graphrag.stores.scope_codec import ScopeBinding, storage_tenant


class ScopedTaskStore(IngestTaskStore):
    def __init__(self, store: IngestTaskStore, binding: ScopeBinding) -> None:
        self.store, self.binding, self.path = store, binding, store.path

    def _public(self, task: IngestTask) -> IngestTask:
        return replace(task, tenant_id=self.binding.scope.tenant_id)

    def _tenant(self, tenant_id: str | None = None) -> str:
        self.binding.check(tenant_id)
        return storage_tenant(self.binding.scope)

    def create(
        self, tenant_id: str, docs: list[dict[str, str]], *, task_id: str | None = None
    ) -> IngestTask:
        owner = self._tenant(tenant_id)
        rows = [
            {**row, "kb_id": self.binding.scope.kb_id, "generation": self.binding.generation}
            for row in docs
        ]
        return self._public(self.store.create(owner, rows, task_id=task_id))

    def get(self, task_id: str, *, tenant_id: str | None = None) -> IngestTask | None:
        task = self.store.get(task_id, tenant_id=self._tenant(tenant_id))
        return self._public(task) if task else None

    def list(
        self, *, tenant_id: str | None = None, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[list[IngestTask], int]:
        tasks, total = self.store.list(
            tenant_id=self._tenant(tenant_id), limit=limit, offset=offset
        )
        return [self._public(task) for task in tasks], total

    def pending(self, limit: int = 10) -> builtins.list[IngestTask]:
        return [
            self._public(t)
            for t in self.store.pending(limit=100000)
            if t.tenant_id == self._tenant()
        ][:limit]

    def requeue_stale(
        self,
        *,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
        exclude_owner: str | None = None,
        tenant_id: str | None = None,
    ) -> builtins.list[str]:
        return self.store.requeue_stale(
            stale_seconds=stale_seconds,
            exclude_owner=exclude_owner,
            tenant_id=self._tenant(tenant_id),
        )

    def touch(self, task_id: str, *, owner: str | None = None) -> bool:
        return self.get(task_id) is not None and self.store.touch(task_id, owner=owner)

    def transition(
        self,
        task_id: str,
        status: IngestStatus,
        *,
        message: str = "",
        owner: str | None = None,
        counts: dict[str, int] | None = None,
    ) -> IngestTask:
        if self.get(task_id) is None:
            raise KeyError(task_id)
        return self._public(
            self.store.transition(task_id, status, message=message, owner=owner, counts=counts)
        )

    def remap_documents(self, mapping: dict[str, str], *, tenant_id: str) -> None:
        self.store.remap_documents(mapping, tenant_id=self._tenant(tenant_id))
