"""Bounded process-local circuit state; contains no prompts, credentials or usage."""

from functools import lru_cache

from agentic_graphrag.llm.circuit import CircuitBreakerSet


@lru_cache(maxsize=256)
def role_circuits(identity: str) -> CircuitBreakerSet:
    return CircuitBreakerSet()
