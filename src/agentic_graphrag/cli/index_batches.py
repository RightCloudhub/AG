"""Tenant-preserving batches for the legacy snapshot indexing command."""

from collections import defaultdict
from collections.abc import Iterator

from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.embedding_batch import BatchEmbedder, embed_with_retry
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig


def tenant_batches(chunks: list[ChunkRecord], size: int) -> Iterator[list[ChunkRecord]]:
    groups: dict[str, list[ChunkRecord]] = defaultdict(list)
    for chunk in chunks:
        groups[chunk.tenant_id].append(chunk)
    for group in groups.values():
        for offset in range(0, len(group), size):
            yield group[offset : offset + size]


def embed_chunks(
    chunks: list[ChunkRecord], embed: BatchEmbedder, *, config: IngestWorkerConfig
) -> None:
    for batch in tenant_batches(chunks, config.embedding_batch_size):
        vectors = embed_with_retry(
            embed, [c.text for c in batch], config=config, heartbeat=lambda: None
        )
        if len(vectors) != len(batch):
            raise ValueError("Embedding response count does not match batch")
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_model = config.embedding_model
