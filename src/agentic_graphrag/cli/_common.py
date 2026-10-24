"""Shared CLI helpers (dirs, graph store open)."""

from __future__ import annotations

import sys

from agentic_graphrag.config import AppConfig, Settings, resolve_path
from agentic_graphrag.core.ports.capabilities import Closable
from agentic_graphrag.core.ports.stores import GraphStore
from agentic_graphrag.stores.factory import GraphBackend


def _ensure_dirs(cfg: AppConfig) -> None:
    for path in (
        cfg.paths.data_dir,
        cfg.paths.raw_docs_dir,
        cfg.paths.processed_dir,
        cfg.paths.cache_dir,
        cfg.paths.indexes_dir,
    ):
        resolve_path(path).mkdir(parents=True, exist_ok=True)


def _neo4j_unavailable_hint(uri: str, exc: BaseException) -> str:
    return (
        f"Neo4j unavailable at {uri}: {exc}\n"
        "  Offline dry-run:  agr-build-graph --triples … --no-llm [--memory-graph]\n"
        "  Start Neo4j:      docker compose up -d\n"
        "  Offline eval:     agr-run-cases --no-llm  (loads seed triples itself)"
    )


def _open_graph_store(
    settings: Settings,
    *,
    memory: bool = False,
    allow_memory_fallback: bool = False,
) -> tuple[GraphStore, GraphBackend]:
    """Open a GraphStore.

    - ``memory=True`` → always InMemoryGraphStore (process-local).
    - else try Neo4j; on failure optionally fall back to memory (seed / offline paths).
    """
    if memory:
        return _memory_graph()
    return _try_neo4j(settings, allow_memory_fallback=allow_memory_fallback)


def _memory_graph() -> tuple[GraphStore, GraphBackend]:
    from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

    return InMemoryGraphStore(), GraphBackend.MEMORY


def _try_neo4j(
    settings: Settings, *, allow_memory_fallback: bool
) -> tuple[GraphStore, GraphBackend]:
    from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore

    store: GraphStore | None = None
    try:
        store = Neo4jGraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        store.ping()
        return store, GraphBackend.NEO4J
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


def _close_quiet(store: object) -> None:
    if not isinstance(store, Closable):
        return
    try:
        store.close()
    except Exception:
        pass
