"""Embedding selection at the ingestion composition boundary."""

from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.knowledge.embedding_batch import BatchEmbedder
from agentic_graphrag.knowledge.index_config import IngestWorkerConfig


def worker_config(cfg: AppConfig, *, live: bool) -> IngestWorkerConfig:
    knobs = cfg.indexing
    return IngestWorkerConfig(
        chunk_size_tokens=cfg.knowledge.chunk_size_tokens,
        chunk_overlap_tokens=cfg.knowledge.chunk_overlap_tokens,
        tokenizer=cfg.knowledge.tokenizer,
        embedding_batch_size=knobs.embedding_batch_size,
        embedding_max_attempts=knobs.embedding_max_attempts,
        retry_base_seconds=knobs.retry_base_seconds,
        retry_max_seconds=knobs.retry_max_seconds,
        embedding_model=cfg.llm.embedding_model if live else "offline-hash-v1",
    )


def batch_embedder(cfg: AppConfig, settings: Settings, *, live: bool) -> BatchEmbedder:
    if live:
        from agentic_graphrag.llm.factory import build_llm_provider

        return build_llm_provider(cfg=cfg, settings=settings).embed_many
    from agentic_graphrag.llm.provider import MockLLMProvider

    provider = MockLLMProvider()
    return lambda texts: [provider.embed(text) for text in texts]
