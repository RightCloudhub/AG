"""Persistent ingest task state machine (ENT-02 / ENT-05)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class IngestStatus(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"
    EMPTY = "empty"


_ALLOWED_TRANSITIONS = {
    IngestStatus.QUEUED: {IngestStatus.EXTRACTING, IngestStatus.FAILED},
    IngestStatus.EXTRACTING: {IngestStatus.REVIEW, IngestStatus.DONE, IngestStatus.FAILED},
    IngestStatus.REVIEW: {IngestStatus.DONE, IngestStatus.FAILED},
}


@dataclass
class IngestTask:
    id: str
    tenant_id: str
    docs: list[dict[str, str]]
    status: str = IngestStatus.QUEUED.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> IngestTask:
        return cls(
            id=str(value.get("id") or uuid.uuid4()),
            tenant_id=str(value.get("tenant_id") or "default"),
            docs=list(value.get("docs") or []),
            status=str(value.get("status") or IngestStatus.QUEUED.value),
            created_at=float(value.get("created_at") or time.time()),
            updated_at=float(value.get("updated_at") or time.time()),
            message=str(value.get("message") or ""),
        )


class IngestTaskStore:
    """Thread-safe last-write-wins JSONL task store."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._tasks: dict[str, IngestTask] = {}
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            self._load()

    def create(
        self, tenant_id: str, docs: list[dict[str, str]], *, task_id: str | None = None
    ) -> IngestTask:
        status = IngestStatus.QUEUED if docs else IngestStatus.EMPTY
        task = IngestTask(
            id=task_id or str(uuid.uuid4()),
            tenant_id=tenant_id,
            docs=docs,
            status=status.value,
            message="Queued for background indexing" if docs else "No documents received",
        )
        with self._lock:
            self._tasks[task.id] = task
            self._persist(task)
        return task

    def get(self, task_id: str, *, tenant_id: str | None = None) -> IngestTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or (tenant_id is not None and task.tenant_id != tenant_id):
                return None
            return IngestTask.from_dict(task.to_dict())

    def pending(self, limit: int = 10) -> list[IngestTask]:
        with self._lock:
            tasks = [t for t in self._tasks.values() if t.status == IngestStatus.QUEUED]
        tasks.sort(key=lambda task: task.created_at)
        return [IngestTask.from_dict(task.to_dict()) for task in tasks[:limit]]

    def transition(self, task_id: str, status: IngestStatus, *, message: str = "") -> IngestTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            current = IngestStatus(task.status)
            allowed = _ALLOWED_TRANSITIONS.get(current, set())
            if status not in allowed:
                raise ValueError(f"Invalid ingest transition: {current.value} -> {status.value}")
            task.status = status.value
            task.updated_at = time.time()
            task.message = message
            self._persist(task)
            return IngestTask.from_dict(task.to_dict())

    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                task = IngestTask.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            self._tasks[task.id] = task

    def _persist(self, task: IngestTask) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(task.to_dict(), ensure_ascii=False) + "\n")
