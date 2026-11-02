"""Run-scoped cross-encoder scores, graph policy, and atomic RRF fallback."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass

from agentic_graphrag.config_rerank import RerankConfig
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.llm.circuit import CircuitBreaker
from agentic_graphrag.retrieval.rerank_text import candidate_text, score_key, with_meta

logger = logging.getLogger(__name__)


class RerankCircuitOpen(RuntimeError):
    """The upstream is cooling down after consecutive failed rerank operations."""


@dataclass
class RerankStats:
    elapsed_ms: float = 0.0
    requests: int = 0
    scored_pairs: int = 0
    cache_hits: int = 0
    fallbacks: int = 0


class CrossEncoderReranker:
    """One instance per query; concurrent branches share its protected score cache."""

    def __init__(self, cfg: RerankConfig, *, circuit: CircuitBreaker | None = None):
        self.cfg = cfg
        self.circuit = circuit or CircuitBreaker(
            failure_threshold=cfg.failure_threshold, cooldown_seconds=cfg.cooldown_seconds
        )
        self._lock = threading.Lock()
        self.reset_run()

    def reset_run(self) -> None:
        self.stats = RerankStats()
        self._scores: dict[tuple[str, str, str], tuple[str, float]] = {}

    @property
    def fallbacks(self) -> int:
        return self.stats.fallbacks

    def telemetry(self) -> dict[str, str | int | float]:
        return {"model": self.cfg.model, **asdict(self.stats)}

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        if not candidates:
            return []
        started = time.perf_counter()
        with self._lock:
            try:
                return self._rerank(query, candidates)
            except Exception as exc:  # noqa: BLE001 — upstream failure cannot drop evidence
                if not isinstance(exc, RerankCircuitOpen):
                    self.circuit.record_failure()
                self.stats.fallbacks += 1
                # WT2 helper is not on this branch. Never log query text or response bodies.
                logger.warning("rerank degraded: %s; preserving RRF order", type(exc).__name__)
                return [with_meta(c, rerank_fallback=type(exc).__name__) for c in candidates]
            finally:
                self.stats.elapsed_ms += (time.perf_counter() - started) * 1000

    def _rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        exempt = self.cfg.graph_policy == "exempt"
        active = [c for c in candidates if not (exempt and c.is_graph())]
        scores = self._cached_scores(query, active)
        scored = [self._annotate(c, score) for c, score in zip(active, scores, strict=True)]
        scored.sort(key=lambda c: -float(c.metadata["rerank_score"]))
        if not exempt:
            return scored
        ordered = iter(scored)
        # Preserve each graph candidate's RRF slot, including slots outside top_k.
        return [
            with_meta(c, rerank_exempt=True) if c.is_graph() else next(ordered) for c in candidates
        ]

    def _cached_scores(self, query: str, candidates: list[Candidate]) -> list[float]:
        keys = [score_key(query, c, model=self.cfg.model) for c in candidates]
        texts = [candidate_text(c)[: self.cfg.max_chars] for c in candidates]
        pending = {}
        for key, text in zip(keys, texts, strict=True):
            cached = self._scores.get(key)
            if cached is None or cached[0] != text:
                pending[key] = text
            else:
                self.stats.cache_hits += 1
        updates = self._fetch(query, pending)
        # Commit only after every batch succeeds; partial success never changes RRF order.
        self._scores.update(updates)
        return [self._scores[key][1] for key in keys]

    def _fetch(
        self, query: str, pending: dict[tuple[str, str, str], str]
    ) -> dict[tuple[str, str, str], tuple[str, float]]:
        if not pending:
            return {}
        if not self.circuit.allow():
            raise RerankCircuitOpen()
        entries = list(pending.items())
        updates: dict[tuple[str, str, str], tuple[str, float]] = {}
        for start in range(0, len(entries), self.cfg.batch_size):
            batch = entries[start : start + self.cfg.batch_size]
            scores = self._score(query[: self.cfg.max_query_chars], [text for _, text in batch])
            if len(scores) != len(batch) or not all(math.isfinite(s) for s in scores):
                raise ValueError("rerank scores must be finite and complete")
            self.stats.scored_pairs += len(batch)
            updates.update(
                (key, (text, score)) for (key, text), score in zip(batch, scores, strict=True)
            )
        self.circuit.record_success()
        return updates

    def _annotate(self, candidate: Candidate, score: float) -> Candidate:
        if candidate.is_graph() and self.cfg.graph_policy == "floor":
            score = max(score, self.cfg.graph_floor)
        return with_meta(candidate, rerank_score=score, rerank="cross_encoder")

    def _score(self, query: str, texts: list[str]) -> list[float]:
        raise NotImplementedError
