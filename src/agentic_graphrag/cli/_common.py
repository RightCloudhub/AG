"""Shared CLI helpers (dirs, graph store open)."""

from __future__ import annotations

import sys
from typing import Any

from agentic_graphrag.config import resolve_path


def _ensure_dirs(cfg) -> None:
    for key in ("data_dir", "raw_docs_dir", "processed_dir", "cache_dir", "indexes_dir"):
        resolve_path(getattr(cfg.paths, key)).mkdir(parents=True, exist_ok=True)

def _neo4j_unavailable_hint(uri: str, exc: BaseException) -> str:
    return (
        f"Neo4j unavailable at {uri}: {exc}\n"
        "  Offline dry-run:  agr-build-graph --triples … --no-llm [--memory-graph]\n"
        "  Start Neo4j:      docker compose up -d\n"
        "  Offline eval:     agr-run-cases --no-llm  (loads seed triples itself)"
    )

def _open_graph_store(
    settings: Any,
    *,
    memory: bool = False,
    allow_memory_fallback: bool = False,
) -> tuple[Any, str]:
    """Open a GraphStore.

    - ``memory=True`` → always InMemoryGraphStore (process-local).
    - else try Neo4j; on failure optionally fall back to memory (seed / offline paths).
    """
    if memory:
        return _memory_graph()
    return _try_neo4j(settings, allow_memory_fallback=allow_memory_fallback)


def _memory_graph() -> tuple[Any, str]:
    from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

    return InMemoryGraphStore(), "memory"


def _try_neo4j(settings: Any, *, allow_memory_fallback: bool) -> tuple[Any, str]:
    from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore

    store: Any = None
    try:
        store = Neo4jGraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        store.ping()
        return store, "neo4j"
    except Exception as exc:
        _close_quiet(store)
        if allow_memory_fallback:
            print(
                f"Warning: Neo4j unavailable at {settings.neo4j_uri} ({exc}); "
                "falling back to in-memory graph (process-local, not persisted).",
                file=sys.stderr,
            )
            return _memory_graph()
        print(_neo4j_unavailable_hint(settings.neo4j_uri, exc), file=sys.stderr)
        sys.exit(1)


def _close_quiet(store: Any) -> None:
    if store is None:
        return
    try:
        store.close()
    except Exception:
        pass

