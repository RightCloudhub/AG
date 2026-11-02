"""Second-stage lexical, HTTP and optional local rerankers behind one protocol."""

from __future__ import annotations

import re
from functools import lru_cache

from agentic_graphrag.config_rerank import RerankConfig
from agentic_graphrag.core.constants import SCORE_PRECISION
from agentic_graphrag.core.enums import RerankKind
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.llm.circuit import CircuitBreaker
from agentic_graphrag.observability.degradation import ChannelHealth
from agentic_graphrag.retrieval.fusion import Reranker
from agentic_graphrag.retrieval.rerank_http import (
    HttpCrossEncoderReranker as HttpCrossEncoderReranker,
)
from agentic_graphrag.retrieval.rerank_text import candidate_text, with_meta

_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_STOP = frozenset(
    "a an the of to in on at for by with is are was were be who what which whom whose "
    "how when where did does do and or that this these those company companies".split()
)
_PRIOR_WEIGHT = 0.5


def _terms(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if t.lower() not in _STOP}


class LexicalReranker:
    """Query-term coverage + RRF prior; ties keep the fused order."""

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        q = _terms(query)
        if not q or len(candidates) < 2:
            return list(candidates)
        scored = []
        for rank, c in enumerate(candidates):
            coverage = len(q & _terms(candidate_text(c))) / len(q)
            prior = _PRIOR_WEIGHT / (1 + rank)
            scored.append((coverage + prior, rank, c))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [
            with_meta(c, rerank_score=round(s, SCORE_PRECISION), rerank="lexical")
            for s, _r, c in scored
        ]


@lru_cache(maxsize=32)
def _shared_circuit(identity: str, threshold: int, cooldown: float) -> CircuitBreaker:
    """Share upstream health across requests, never query text or scores."""
    return CircuitBreaker(failure_threshold=threshold, cooldown_seconds=cooldown)


def build_reranker(cfg: RerankConfig, *, health: ChannelHealth | None = None) -> Reranker | None:
    """Cross-encoder fallbacks are marked on the run's ``health`` (O-9)."""
    kind = RerankKind(cfg.kind)
    if kind is RerankKind.NONE:
        return None
    if kind is RerankKind.LEXICAL:
        return LexicalReranker()
    circuit = _shared_circuit(
        f"{kind}:{cfg.endpoint}:{cfg.model}", cfg.failure_threshold, cfg.cooldown_seconds
    )
    if kind is RerankKind.HTTP:
        return HttpCrossEncoderReranker(cfg.endpoint, cfg=cfg, circuit=circuit, health=health)
    from agentic_graphrag.retrieval.rerank_local import LocalCrossEncoderReranker

    return LocalCrossEncoderReranker(cfg, circuit=circuit, health=health)
