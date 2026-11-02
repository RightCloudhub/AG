"""TEI-compatible scores with bounded requests and one retry on HTTP 5xx."""

from __future__ import annotations

import httpx

from agentic_graphrag.config_rerank import RerankConfig
from agentic_graphrag.llm.circuit import CircuitBreaker
from agentic_graphrag.observability.degradation import ChannelHealth
from agentic_graphrag.retrieval.rerank_base import CrossEncoderReranker

_SERVER_ERROR = 500
_HTTP_ATTEMPTS = 2


class HttpCrossEncoderReranker(CrossEncoderReranker):
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 2.0,
        max_chars: int = 2000,
        cfg: RerankConfig | None = None,
        circuit: CircuitBreaker | None = None,
        health: ChannelHealth | None = None,
    ):
        if not endpoint:
            raise ValueError("HttpCrossEncoderReranker needs an endpoint")
        config = cfg or RerankConfig(timeout_seconds=timeout_seconds, max_chars=max_chars)
        super().__init__(config, circuit=circuit, health=health)
        self.endpoint = endpoint
        self.timeout_seconds = config.timeout_seconds
        self.max_chars = config.max_chars

    def _score(self, query: str, texts: list[str]) -> list[float]:
        for attempt in range(_HTTP_ATTEMPTS):
            self.stats.requests += 1
            response = httpx.post(
                self.endpoint,
                json={"query": query, "texts": texts, "truncate": True},
                timeout=self.timeout_seconds,
            )
            if response.status_code < _SERVER_ERROR or attempt == _HTTP_ATTEMPTS - 1:
                response.raise_for_status()
                return _parse_scores(response, count=len(texts))
        raise AssertionError("unreachable")


def _parse_scores(response: httpx.Response, *, count: int) -> list[float]:
    rows = response.json()
    if isinstance(rows, dict):
        rows = rows.get("results")
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("rerank response must contain every input index exactly once")
    scores = {}
    for row in rows:
        index = row["index"]
        if type(index) is not int or index not in range(count) or index in scores:
            raise ValueError("invalid or duplicate rerank index")
        scores[index] = float(row.get("score", row.get("relevance_score")))
    return [scores[index] for index in range(count)]
