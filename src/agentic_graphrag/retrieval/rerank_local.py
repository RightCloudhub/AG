"""Optional sentence-transformers adapter; neither import nor model load is eager."""

from __future__ import annotations

import importlib
import threading
from functools import lru_cache
from typing import Any

from agentic_graphrag.retrieval.rerank_base import CrossEncoderReranker


@lru_cache(maxsize=2)
def _load_model(model: str, max_length: int) -> Any:
    try:
        module = importlib.import_module("sentence_transformers")
    except ImportError as exc:
        raise RuntimeError(
            "Local reranking requires setup.sh --extras dev,webapp,rerank-local"
        ) from exc
    return module.CrossEncoder(model, max_length=max_length, trust_remote_code=False)


_MODEL_LOCK = threading.Lock()


class LocalCrossEncoderReranker(CrossEncoderReranker):
    """Shares model weights, while inherited scores and telemetry stay per run."""

    def _score(self, query: str, texts: list[str]) -> list[float]:
        self.stats.requests += 1
        with _MODEL_LOCK:
            model = _load_model(self.cfg.model, self.cfg.max_length)
            scores = model.predict(
                [(query, text) for text in texts],
                batch_size=self.cfg.batch_size,
                show_progress_bar=False,
            )
        return [float(score) for score in scores]
