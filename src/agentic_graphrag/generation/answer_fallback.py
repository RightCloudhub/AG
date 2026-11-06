"""Explicit fallback policy for legacy Schema and LLM-only knowledge bases."""

from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.generation.offline_answer import offline_answer


def _offline_or_abstain(
    chain: ReasoningChain, evidence: list[Candidate], conclusions: str
) -> ReasoningChain:
    if chain.metadata.get("llm_only"):
        chain.honest_fallback("Required model capability is unavailable")
        return chain
    return offline_answer(chain, evidence, conclusions)
