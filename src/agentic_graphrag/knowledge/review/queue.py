"""In-process / JSONL-backed review queue (P3-KG-03 / FR-KG-06)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ReviewType(StrEnum):
    EXTRACTION = "extraction"
    RESOLUTION = "resolution"
    CONFLICT = "conflict"
    SPOTCHECK = "spotcheck"
    FEEDBACK = "feedback"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    SKIP = "skip"


@dataclass
class ReviewItem:
    id: str
    type: str
    payload: dict[str, Any]
    status: str = ReviewStatus.PENDING.value
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    reviewer: str = ""
    decision_note: str = ""
    batch_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewItem:
        return cls(
            id=str(d.get("id") or uuid.uuid4()),
            type=str(d.get("type") or ReviewType.EXTRACTION.value),
            payload=dict(d.get("payload") or {}),
            status=str(d.get("status") or ReviewStatus.PENDING.value),
            confidence=float(d.get("confidence") or 0.0),
            created_at=float(d.get("created_at") or time.time()),
            decided_at=d.get("decided_at"),
            reviewer=str(d.get("reviewer") or ""),
            decision_note=str(d.get("decision_note") or ""),
            batch_id=str(d.get("batch_id") or ""),
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
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = ReviewItem.from_dict(json.loads(line))
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
    ) -> ReviewItem:
        item = ReviewItem(
            id=item_id or str(uuid.uuid4()),
            type=type.value if isinstance(type, ReviewType) else str(type),
            payload=payload,
            confidence=confidence,
            batch_id=batch_id,
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
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReviewItem]:
        with self._lock:
            items = list(self._items.values())
        items = _filter_items(
            items,
            status=status,
            type=type,
            min_confidence=min_confidence,
            max_confidence=max_confidence,
        )
        items.sort(key=lambda i: i.created_at)
        return items[offset : offset + limit]

    def decide(
        self,
        item_id: str,
        decision: ReviewDecision | str,
        *,
        reviewer: str = "",
        note: str = "",
    ) -> ReviewItem:
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(item_id)
            dec = decision.value if isinstance(decision, ReviewDecision) else str(decision)
            if dec == ReviewDecision.APPROVE.value:
                item.status = ReviewStatus.APPROVED.value
            elif dec == ReviewDecision.REJECT.value:
                item.status = ReviewStatus.REJECTED.value
            else:
                item.status = ReviewStatus.SKIPPED.value
            item.reviewer = reviewer
            item.decision_note = note
            item.decided_at = time.time()
            self._persist(item)
            return item

    def counts(self) -> dict[str, int]:
        with self._lock:
            out: dict[str, int] = {}
            for item in self._items.values():
                out[item.status] = out.get(item.status, 0) + 1
            out["total"] = len(self._items)
            return out


def _filter_items(
    items: list[ReviewItem],
    *,
    status: str | None,
    type: str | None,
    min_confidence: float | None,
    max_confidence: float | None,
) -> list[ReviewItem]:
    preds = []
    if status:
        preds.append(lambda i, s=status: i.status == s)
    if type:
        preds.append(lambda i, t=type: i.type == t)
    if min_confidence is not None:
        preds.append(lambda i, m=min_confidence: i.confidence >= m)
    if max_confidence is not None:
        preds.append(lambda i, m=max_confidence: i.confidence <= m)
    if not preds:
        return items
    return [i for i in items if all(p(i) for p in preds)]
