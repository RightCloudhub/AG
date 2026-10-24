"""LangGraph checkpointer factory (P2-AG-03 / ADR-005).

Framework owns persistence; Memory semantics stay in :mod:`memory`.
Default backend is process-local ``MemorySaver`` (survives across invokes
with the same ``thread_id`` and enables ``get_state`` / history for audit).

Optional ``sqlite`` backend requires ``langgraph-checkpoint-sqlite``
(+ ``aiosqlite``); raises a clear error if the requested integration is missing.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver

from agentic_graphrag.agent.graph_types import AgentCheckpointer


class CheckpointerBackend(StrEnum):
    MEMORY = "memory"
    SQLITE = "sqlite"


_BACKEND_ALIASES = {
    "mem": CheckpointerBackend.MEMORY,
    "inmemory": CheckpointerBackend.MEMORY,
    "in_memory": CheckpointerBackend.MEMORY,
    "sql": CheckpointerBackend.SQLITE,
    "disk": CheckpointerBackend.SQLITE,
    "file": CheckpointerBackend.SQLITE,
}
_DEFAULT_SQLITE_PATH = "data/cache/checkpoints.sqlite"

__all__ = [
    "CheckpointerBackend",
    "make_checkpointer",
    "default_checkpointer",
]


def make_checkpointer(
    backend: CheckpointerBackend = CheckpointerBackend.MEMORY,
    *,
    path: str | Path | None = None,
) -> AgentCheckpointer:
    """Build a LangGraph checkpointer.

    Parameters
    ----------
    backend:
        ``memory`` — :class:`langgraph.checkpoint.memory.MemorySaver` (default).
        ``sqlite`` — durable SQLite (optional extra package).
    path:
        SQLite file path when ``backend=sqlite``. Defaults to
        ``data/cache/checkpoints.sqlite`` under the repo root.
    """
    name = (backend or CheckpointerBackend.MEMORY).strip().lower()
    choice = _BACKEND_ALIASES.get(name)
    if choice is None:
        try:
            choice = CheckpointerBackend(name)
        except ValueError as exc:
            raise ValueError(
                f"Unknown checkpointer backend: {backend!r} "
                f"(use {CheckpointerBackend.MEMORY.value!r} "
                f"or {CheckpointerBackend.SQLITE.value!r})"
            ) from exc
    if choice is CheckpointerBackend.MEMORY:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    return _make_sqlite_checkpointer(path)


def default_checkpointer() -> AgentCheckpointer:
    """Default process-local checkpointer for agent compiles."""
    return make_checkpointer(CheckpointerBackend.MEMORY)


def _make_sqlite_checkpointer(path: str | Path | None) -> AgentCheckpointer:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise ImportError(
            "SQLite checkpointer requires langgraph-checkpoint-sqlite. "
            "Install it or use backend='memory'."
        ) from exc

    from agentic_graphrag.config import resolve_path

    db_path = Path(path) if path else resolve_path(_DEFAULT_SQLITE_PATH)
    if not db_path.is_absolute():
        db_path = resolve_path(str(db_path))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        saver = SqliteSaver(connection)
        if not isinstance(saver, BaseCheckpointSaver):
            raise TypeError("SQLite saver must implement BaseCheckpointSaver")
        return saver
    except Exception:
        connection.close()
        raise
