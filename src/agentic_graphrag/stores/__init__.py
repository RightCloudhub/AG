"""Storage adapters (Repository pattern)."""

from agentic_graphrag.stores.interfaces import (
    DocStore,
    FulltextStore,
    GraphStore,
    VectorStore,
)

__all__ = ["DocStore", "FulltextStore", "GraphStore", "VectorStore"]
