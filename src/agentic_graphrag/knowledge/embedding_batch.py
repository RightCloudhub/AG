"""Bounded embedding retries; authentication errors are terminal and sanitized."""

import random
import time
from collections.abc import Callable

import httpx

from agentic_graphrag.knowledge.index_config import IngestWorkerConfig

BatchEmbedder = Callable[[list[str]], list[list[float]]]


def status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(getattr(exc, "response", None), "status_code", None)
    return int(value) if value is not None else None


def retryable(exc: Exception) -> bool:
    status = status_code(exc)
    return (
        status == 429
        or (status is not None and 500 <= status < 600)
        or isinstance(exc, (TimeoutError, httpx.TimeoutException))
    )


def embed_with_retry(
    embed: BatchEmbedder,
    texts: list[str],
    *,
    config: IngestWorkerConfig,
    heartbeat: Callable[[], None],
) -> list[list[float]]:
    for attempt in range(config.embedding_max_attempts):
        heartbeat()
        try:
            return embed(texts)
        except Exception as exc:
            if not retryable(exc) or attempt + 1 == config.embedding_max_attempts:
                raise
            ceiling = min(config.retry_max_seconds, config.retry_base_seconds * 2**attempt)
            time.sleep(random.uniform(0, ceiling))
    raise AssertionError("unreachable")


def single_adapter(embed: Callable[[str], list[float]]) -> BatchEmbedder:
    return lambda texts: [embed(text) for text in texts]
