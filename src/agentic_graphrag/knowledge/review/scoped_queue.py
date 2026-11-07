"""Bind review reads, decisions and execution records to one knowledge generation."""

from __future__ import annotations

import builtins
from dataclasses import replace
from typing import Any

from agentic_graphrag.core.constants import DEFAULT_PAGE_SIZE
from agentic_graphrag.knowledge.review.queue import (
    ReviewDecision,
    ReviewItem,
    ReviewQueue,
    ReviewStatus,
    ReviewType,
)
from agentic_graphrag.stores.scope_codec import ScopeBinding, storage_tenant


class ScopedReviewQueue(ReviewQueue):
    def __init__(self, store: ReviewQueue, binding: ScopeBinding) -> None:
        self.store, self.binding, self.path = store, binding, store.path

    def _public(self, item: ReviewItem) -> ReviewItem:
        return replace(item, tenant_id=self.binding.scope.tenant_id)

    def _tenant(self, tenant_id: str | None = None) -> str:
        self.binding.check(tenant_id)
        return storage_tenant(self.binding.scope)

    def enqueue(
        self,
        type: ReviewType | str,
        payload: dict[str, Any],
        *,
        confidence: float = 0.0,
        batch_id: str = "",
        item_id: str | None = None,
        tenant_id: str = "",
    ) -> ReviewItem:
        owner = self._tenant(tenant_id or None)
        return self._public(
            self.store.enqueue(
                type,
                {
                    **payload,
                    "kb_id": self.binding.scope.kb_id,
                    "target_generation": self.binding.generation,
                },
                confidence=confidence,
                batch_id=batch_id,
                item_id=item_id,
                tenant_id=owner,
            )
        )

    def get(self, item_id: str) -> ReviewItem | None:
        item = self.store.get(item_id)
        if item is None or (item.tenant_id or "default") != self._tenant():
            return None
        return self._public(item)

    def list_page(
        self,
        *,
        status: str | None = ReviewStatus.PENDING.value,
        type: str | None = None,
        min_confidence: float | None = None,
        max_confidence: float | None = None,
        tenant_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[builtins.list[ReviewItem], int]:
        items, total = self.store.list_page(
            status=status,
            type=type,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            tenant_id=self._tenant(tenant_id),
            limit=limit,
            offset=offset,
        )
        return [self._public(item) for item in items], total

    def decide(
        self,
        item_id: str,
        decision: ReviewDecision | str,
        *,
        reviewer: str = "",
        note: str = "",
        tenant_id: str | None = None,
        keep_id: str | None = None,
    ) -> ReviewItem:
        return self._public(
            self.store.decide(
                item_id,
                decision,
                reviewer=reviewer,
                note=note,
                tenant_id=self._tenant(tenant_id),
                keep_id=keep_id,
            )
        )

    def record_execution(self, item_id: str, outcome: dict[str, Any]) -> None:
        if self.get(item_id) is None:
            raise KeyError(item_id)
        self.store.record_execution(item_id, outcome)

    def counts(self) -> dict[str, int]:
        counts = {state.value: self.count(status=state.value) for state in ReviewStatus}
        return {**counts, "total": sum(counts.values())}

    def count(
        self,
        *,
        status: str | None = None,
        type: str | None = None,
        tenant_id: str | None = None,
        batch_id: str | None = None,
        doc_id: str | None = None,
        doc_version: int | None = None,
    ) -> int:
        return self.store.count(
            status=status,
            type=type,
            tenant_id=self._tenant(tenant_id),
            batch_id=batch_id,
            doc_id=doc_id,
            doc_version=doc_version,
        )
