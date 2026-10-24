"""In-process / JSONL-backed review queue (P3-KG-03 / FR-KG-06)."""

from __future__ import annotations

import builtins
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.core.constants import DEFAULT_PAGE_SIZE
from agentic_graphrag.core.enums import ReviewDecision as ReviewDecision
from agentic_graphrag.core.enums import ReviewStatus as ReviewStatus
from agentic_graphrag.core.enums import ReviewType as ReviewType
from agentic_graphrag.knowledge.review.filters import filter_items, tenant_allows

logger = logging.getLogger(__name__)


_DECISION_STATUS = {
    ReviewDecision.APPROVE: ReviewStatus.APPROVED,
    ReviewDecision.REJECT: ReviewStatus.REJECTED,
    ReviewDecision.SKIP: ReviewStatus.SKIPPED,
}


class ReviewAlreadyDecided(RuntimeError):
    """Raised when a decided item is decided again (terminal-state protection)."""

    def __init__(self, item: ReviewItem) -> None:
        super().__init__(f"Review item {item.id} is already {item.status}")
        self.item = item


@dataclass
class ReviewItem:
    id: str
    type: ReviewType
    payload: dict[str, Any]
    status: ReviewStatus = ReviewStatus.PENDING
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    reviewer: str = ""
    decision_note: str = ""
    batch_id: str = ""
    tenant_id: str = ""
    # BL-03: what the review executor did with the decision (empty until run).
    execution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewItem:
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            type=ReviewType(data.get("type") or ReviewType.EXTRACTION),
            payload=dict(data.get("payload", {})),
            status=ReviewStatus(data.get("status") or ReviewStatus.PENDING),
            confidence=float(data.get("confidence") or 0.0),
            created_at=float(data.get("created_at") or time.time()),
            decided_at=data.get("decided_at"),
            reviewer=str(data.get("reviewer") or ""),
            decision_note=str(data.get("decision_note") or ""),
            batch_id=str(data.get("batch_id") or ""),
            tenant_id=str(data.get("tenant_id") or ""),
            execution=dict(data.get("execution", {})),
        )


class ReviewQueue:
    """Thread-safe review queue with optional JSONL persistence."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._items: dict[str, ReviewItem] = {}
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        """Load persisted items, skipping corrupt lines.

        A single malformed line used to raise straight out of ``__init__`` and
        take ``QueryService`` construction — hence the whole API — down with it
        (docs/design/BUSINESS_LOGIC.md BL-11).
        """
        assert self.path is not None
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = ReviewItem.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("Skipping corrupt review-queue line %s:%d", self.path, lineno)
                continue
            self._items[item.id] = item

    def _persist(self, item: ReviewItem) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")

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
        item = ReviewItem(
            id=item_id or str(uuid.uuid4()),
            type=ReviewType(type),
            payload=payload,
            confidence=confidence,
            batch_id=batch_id,
            tenant_id=tenant_id,
        )
        with self._lock:
            self._items[item.id] = item
            self._persist(item)
        return item

    def get(self, item_id: str) -> ReviewItem | None:
        with self._lock:
            return self._items.get(item_id)

    def list(
        self,
        *,
        status: str | None = ReviewStatus.PENDING.value,
        type: str | None = None,
        min_confidence: float | None = None,
        max_confidence: float | None = None,
        tenant_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[ReviewItem]:
        return self.list_page(
            status=status,
            type=type,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
        )[0]

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
        """Return one filtered page and its tenant-scoped total."""
        with self._lock:
            items = list(self._items.values())
        items = filter_items(
            items,
            status=status,
            type=type,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
            tenant_id=tenant_id,
        )
        items.sort(key=lambda i: i.created_at)
        total = len(items)
        return items[offset : offset + limit], total

    def decide(
        self,
        item_id: str,
        decision: ReviewDecision | str,
        *,
        reviewer: str = "",
        note: str = "",
        tenant_id: str | None = None,
    ) -> ReviewItem:
        """Record a decision.

        ``tenant_id`` scopes the lookup — without it any operator who knew an id
        could decide another tenant's item (docs/design/BUSINESS_LOGIC.md BL-09). A
        mismatch raises ``KeyError`` so callers answer 404 for "not yours" and
        "not found" alike. Legacy rows with an empty ``tenant_id`` stay
        decidable by any tenant. Re-deciding a decided item raises
        ``ReviewAlreadyDecided`` instead of silently overwriting it (BL-11).
        """
        with self._lock:
            item = self._items.get(item_id)
            if item is None or not tenant_allows(item, tenant_id):
                raise KeyError(item_id)
            if item.status != ReviewStatus.PENDING.value:
                raise ReviewAlreadyDecided(item)
            item.status = _DECISION_STATUS[ReviewDecision(decision)]
            item.reviewer = reviewer
            item.decision_note = note
            item.decided_at = time.time()
            self._persist(item)
            return item

    def record_execution(self, item_id: str, outcome: dict[str, Any]) -> None:
        """Attach the executor's outcome to a decided item (persisted, last write wins)."""
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return
            item.execution = dict(outcome)
            self._persist(item)

    def counts(self) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for item in self._items.values():
                out[item.status] = out.get(item.status, 0) + 1
            out["total"] = len(self._items)
            return out

    def count(
        self,
        *,
        status: str | None = None,
        type: str | None = None,
        tenant_id: str | None = None,
        batch_id: str | None = None,
    ) -> int:
        with self._lock:
            items = list(self._items.values())
        return len(
            filter_items(
                items,
                status=status,
                type=type,
                min_confidence=None,
                max_confidence=None,
                tenant_id=tenant_id,
                batch_id=batch_id,
            )
        )
