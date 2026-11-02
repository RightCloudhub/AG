"""Worker loop and token-based chunking settings."""

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
