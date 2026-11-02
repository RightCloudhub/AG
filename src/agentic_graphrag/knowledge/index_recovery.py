"""Invalidate checkpoints belonging to a previous process-local index generation."""

from agentic_graphrag.core.ports.capabilities import ProcessLocalIndex
from agentic_graphrag.core.ports.stores import DocumentRecord


def stale_stages(doc: DocumentRecord, indexes: dict[str, object]) -> dict[str, str]:
    recorded = doc.metadata.get("index_generations", {})
    return {
        stage: index.index_generation
        for stage, index in indexes.items()
        if isinstance(index, ProcessLocalIndex) and recorded.get(stage) != index.index_generation
    }


def invalidate_stages(doc: DocumentRecord, generations: dict[str, str]) -> None:
    for chunk in doc.chunks:
        for stage in generations:
            setattr(chunk, stage, False)
    doc.metadata.setdefault("index_generations", {}).update(generations)
    doc.metadata["index_complete"] = False
