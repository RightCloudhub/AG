"""Storage adapters (Repository pattern — P2-ARCH-02 / NFR-10)."""

from agentic_graphrag.stores.factory import (
    GraphBackend,
    StoreBundle,
    VectorBackend,
    create_doc_store,
    create_fulltext_store,
    create_graph_store,
    create_live_bundle,
    create_offline_bundle,
    create_vector_store,
)
from agentic_graphrag.stores.interfaces import (
    ChunkRecord,
    DocStore,
    DocumentRecord,
    EntityRecord,
    FulltextStore,
    GraphStore,
    PathRecord,
    RelationRecord,
    VectorStore,
)

__all__ = [
    "ChunkRecord",
    "DocStore",
    "DocumentRecord",
    "EntityRecord",
    "FulltextStore",
    "GraphBackend",
    "GraphStore",
    "PathRecord",
    "RelationRecord",
    "StoreBundle",
    "VectorBackend",
    "VectorStore",
    "create_doc_store",
    "create_fulltext_store",
    "create_graph_store",
    "create_live_bundle",
    "create_offline_bundle",
    "create_vector_store",
]
