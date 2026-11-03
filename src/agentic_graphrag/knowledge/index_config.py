"""Ingest worker, token chunking, indexing and upload capacity configuration."""

from dataclasses import dataclass

from agentic_graphrag.config_knowledge import (
    DEFAULT_CHUNK_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_TOKENIZER,
)
from agentic_graphrag.knowledge.ingest_tasks import DEFAULT_STALE_SECONDS


@dataclass
class IngestWorkerConfig:
    poll_interval_seconds: float = 5.0
    batch_size: int = 10
    chunk_size_tokens: int = DEFAULT_CHUNK_TOKENS
    chunk_overlap_tokens: int = DEFAULT_OVERLAP_TOKENS
    tokenizer: str = DEFAULT_TOKENIZER
    stale_task_seconds: float = DEFAULT_STALE_SECONDS
    embedding_batch_size: int = 32
    embedding_max_attempts: int = 4
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 30.0
    embedding_model: str = "custom"

    def __post_init__(self) -> None:
        if min(self.embedding_batch_size, self.embedding_max_attempts, self.batch_size) < 1:
            raise ValueError("Batch sizes and attempts must be positive")
