"""Indexing and upload capacity configuration."""

from dataclasses import dataclass

from agentic_graphrag.knowledge.ingest_tasks import DEFAULT_STALE_SECONDS


@dataclass
class IngestWorkerConfig:
    poll_interval_seconds: float = 5.0
    batch_size: int = 10
    chunk_size_chars: int = 1200
    chunk_overlap_chars: int = 150
    stale_task_seconds: float = DEFAULT_STALE_SECONDS
    embedding_batch_size: int = 32
    embedding_max_attempts: int = 4
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 30.0
    embedding_model: str = "custom"

    def __post_init__(self) -> None:
        if min(self.embedding_batch_size, self.embedding_max_attempts, self.batch_size) < 1:
            raise ValueError("Batch sizes and attempts must be positive")
