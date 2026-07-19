"""LangGraph checkpointer factory (P2-AG-03 / ADR-005).

Framework owns persistence; Memory semantics stay in :mod:`memory`.
Default backend is process-local ``MemorySaver`` (survives across invokes
with the same ``thread_id`` and enables ``get_state`` / history for audit).

Optional ``sqlite`` backend requires ``langgraph-checkpoint-sqlite``
(+ ``aiosqlite``); falls back to memory with a clear error if missing when
forced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

CheckpointerBackend = Literal["memory", "sqlite"]

__all__ = [
    "CheckpointerBackend",
    "make_checkpointer",
    "default_checkpointer",
]


def make_checkpointer(
    backend: CheckpointerBackend | str = "memory",
    *,
    path: str | Path | None = None,
) -> Any:
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
    name = (backend or "memory").strip().lower()
    if name in ("memory", "mem", "inmemory", "in_memory"):
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    if name in ("sqlite", "sql", "disk", "file"):
        return _make_sqlite_checkpointer(path)

    raise ValueError(f"Unknown checkpointer backend: {backend!r} (use 'memory' or 'sqlite')")


def default_checkpointer() -> Any:
    """Default process-local checkpointer for agent compiles."""
    return make_checkpointer("memory")


def _make_sqlite_checkpointer(path: str | Path | None) -> Any:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise ImportError(
            "SQLite checkpointer requires langgraph-checkpoint-sqlite. "
            "Install it or use backend='memory'."
        ) from exc

    from agentic_graphrag.config import resolve_path

    db_path = Path(path) if path else resolve_path("data/cache/checkpoints.sqlite")
    if not db_path.is_absolute():
        db_path = resolve_path(str(db_path))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # SqliteSaver.from_conn_string returns a context manager in some versions;
    # prefer open connection when available.
    conn_str = str(db_path)
    if hasattr(SqliteSaver, "from_conn_string"):
        cm = SqliteSaver.from_conn_string(conn_str)
        if hasattr(cm, "__enter__"):
            return cm.__enter__()
        return cm
    return SqliteSaver(conn_str)  # type: ignore[call-arg]
