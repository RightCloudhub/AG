"""Bounded task cache used when no persistent ingest-task store is configured."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Any

MAX_CACHED_TASKS = 500
_TASKS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_TASKS_LOCK = Lock()


def remember_task(task_id: str, row: dict[str, Any]) -> None:
    with _TASKS_LOCK:
        _TASKS[task_id] = row
        _TASKS.move_to_end(task_id)
        while len(_TASKS) > MAX_CACHED_TASKS:
            _TASKS.popitem(last=False)


def get_cached_task(task_id: str) -> dict[str, Any] | None:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def list_cached_tasks(*, tenant_id: str, limit: int, offset: int) -> tuple[list, int]:
    with _TASKS_LOCK:
        cached = list(_TASKS.values())
    rows = [row for row in reversed(cached) if row.get("tenant_id") in {None, tenant_id}]
    return rows[offset : offset + limit], len(rows)
