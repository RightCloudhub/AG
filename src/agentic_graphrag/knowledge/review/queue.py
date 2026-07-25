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
    tenant_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewItem:
        coerce: dict[str, tuple[Any, Any]] = {
            "id": (str, str(uuid.uuid4())),
            "type": (str, ReviewType.EXTRACTION.value),
            "payload": (dict, {}),
            "status": (str, ReviewStatus.PENDING.value),
            "confidence": (float, 0.0),
            "created_at": (float, time.time()),
            "reviewer": (str, ""),
            "decision_note": (str, ""),
            "batch_id": (str, ""),
            "tenant_id": (str, ""),
        }
        normalized = dict(data)
        for key, (cast, default) in coerce.items():
            normalized[key] = cast(data.get(key) or default)
        allowed = cls.__dataclass_fields__
        return cls(**{key: value for key, value in normalized.items() if key in allowed})


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
        tenant_id: str = "",
    ) -> ReviewItem:
        item = ReviewItem(
            id=item_id or str(uuid.uuid4()),
            type=type.value if isinstance(type, ReviewType) else str(type),
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
            tenant_id=tenant_id,
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
    tenant_id: str | None,
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
    if tenant_id is not None:
        preds.append(lambda i, t=tenant_id: i.tenant_id == t)
    if not preds:
        return items
    return [i for i in items if all(p(i) for p in preds)]
