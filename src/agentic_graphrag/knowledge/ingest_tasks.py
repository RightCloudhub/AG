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
    # EXTRACTING → QUEUED is the crash-recovery path: a worker killed mid-task
    # would otherwise leave it EXTRACTING forever, invisible to ``pending()``
    # and to any retry (docs/BUSINESS_LOGIC.md BL-06).
    IngestStatus.EXTRACTING: {
        IngestStatus.REVIEW,
        IngestStatus.DONE,
        IngestStatus.FAILED,
        IngestStatus.QUEUED,
    },
    IngestStatus.REVIEW: {IngestStatus.DONE, IngestStatus.FAILED},
}

# A task claimed for longer than this — without a heartbeat — is presumed
# abandoned by a dead worker.
DEFAULT_STALE_SECONDS = 900.0
_REQUEUE_MESSAGE = "Requeued after stale extraction"


@dataclass
class IngestTask:
    id: str
    tenant_id: str
    docs: list[dict[str, str]]
    status: str = IngestStatus.QUEUED.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    message: str = ""
    # Lease holder: the worker token that claimed this task. Empty when unclaimed.
    owner: str = ""

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
            owner=str(value.get("owner") or ""),
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

    def requeue_stale(
        self,
        *,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
        exclude_owner: str | None = None,
    ) -> list[str]:
        """Return EXTRACTING tasks abandoned by a dead worker to QUEUED.

        Without this a worker crash strands the task in EXTRACTING permanently
        — ``pending()`` only yields QUEUED, so nothing ever retries it (BL-06).

        ``exclude_owner`` is the caller's own lease token. A live worker must
        never reclaim its *own* in-flight task: the duplicate run would race the
        original, and the original's terminal transition would then be invalid.
        Workers keep the lease fresh through :meth:`touch`, so ``updated_at``
        only falls behind the cutoff once the owner has genuinely stopped.

        The lease is authoritative inside one process. Across processes this
        store is last-write-wins with no shared lock, so a claim race remains
        possible — hence :meth:`transition` refusing invalid moves rather than
        trusting the lease alone.

        Returns the ids that were requeued.
        """
        cutoff = time.time() - max(0.0, stale_seconds)
        with self._lock:
            stale = [
                task.id
                for task in self._tasks.values()
                if _is_abandoned(task, cutoff=cutoff, exclude_owner=exclude_owner)
            ]
        requeued: list[str] = []
        for task_id in stale:
            try:
                self.transition(task_id, IngestStatus.QUEUED, message=_REQUEUE_MESSAGE, owner="")
            except (KeyError, ValueError):
                continue
            requeued.append(task_id)
        return requeued

    def touch(self, task_id: str, *, owner: str | None = None) -> bool:
        """Heartbeat: mark a task as still being worked on.

        Without it ``requeue_stale`` cannot tell a dead worker from a slow one
        and would reclaim a task that is still in flight. Returns False when the
        task is gone or the lease belongs to a different worker.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if owner is not None and task.owner and task.owner != owner:
                return False
            task.updated_at = time.time()
            if owner is not None:
                task.owner = owner
            self._persist(task)
            return True

    def transition(
        self,
        task_id: str,
        status: IngestStatus,
        *,
        message: str = "",
        owner: str | None = None,
    ) -> IngestTask:
        """Move a task to ``status``. ``owner`` replaces the lease when given."""
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
            if owner is not None:
                task.owner = owner
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


def _is_abandoned(task: IngestTask, *, cutoff: float, exclude_owner: str | None) -> bool:
    """True when a claimed task has stopped reporting and is not ours."""
    if task.status != IngestStatus.EXTRACTING:
        return False
    if task.updated_at >= cutoff:
        return False
    return exclude_owner is None or task.owner != exclude_owner
