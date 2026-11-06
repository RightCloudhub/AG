"""Bind existing repository ports to a single authorized knowledge scope."""

from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.ports.capabilities import ModelVectorSearch, VectorModelRebuild
from agentic_graphrag.stores.factory import StoreBundle
from agentic_graphrag.stores.scope_codec import ScopeBinding
from agentic_graphrag.stores.scoped_documents import ScopedDocStore
from agentic_graphrag.stores.scoped_graph import ScopedGraphStore
from agentic_graphrag.stores.scoped_indexes import (
    ScopedFulltextStore,
    ScopedModelVectorStore,
    ScopedVectorStore,
)


def scope_bundle(
    bundle: StoreBundle, scope: KnowledgeScope, *, generation: str = "1", read_only: bool = False
) -> StoreBundle:
    if isinstance(bundle.graph, ScopedGraphStore):
        binding = bundle.graph.binding
        if (binding.scope, binding.generation, binding.read_only) != (scope, generation, read_only):
            raise ValueError("Cannot rebind an already scoped bundle")
        return bundle
    binding = ScopeBinding(scope, generation, read_only)
    binding.check()
    vector_type = (
        ScopedModelVectorStore
        if isinstance(bundle.vector, (VectorModelRebuild, ModelVectorSearch))
        else ScopedVectorStore
    )
    return StoreBundle(
        graph=ScopedGraphStore(bundle.graph, binding),
        vector=vector_type(bundle.vector, binding),
        fulltext=ScopedFulltextStore(bundle.fulltext, binding),
        docs=ScopedDocStore(bundle.docs, binding),
        graph_backend=bundle.graph_backend,
        vector_backend=bundle.vector_backend,
    )
