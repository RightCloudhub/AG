"""LLM resilience settings (ARCH_OPTIMIZATION_V2 O-8), kept out of config.py's line budget."""

from __future__ import annotations

from pydantic import BaseModel


class LLMResilienceConfig(BaseModel):
    """Per-route circuit breakers + optional STRONG → LIGHT degradation.

    Defaults reproduce V1 (2 consecutive failures, 20 s cooldown, no window,
    no fallback).
    """

    failure_threshold: int = 2
    cooldown_seconds: float = 20.0
    window_seconds: float = 0.0  # >0 enables the failure-rate rule
    failure_rate: float = 0.5
    min_calls: int = 5
    fallback_to_light: bool = False

    def breaker_template(self):
        from agentic_graphrag.llm.circuit import CircuitBreaker

        return CircuitBreaker(
            failure_threshold=self.failure_threshold,
            cooldown_seconds=self.cooldown_seconds,
            window_seconds=self.window_seconds,
            failure_rate=self.failure_rate,
            min_calls=self.min_calls,
        )
