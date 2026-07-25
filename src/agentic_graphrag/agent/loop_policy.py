"""Shared agent-loop routing policy."""

from agentic_graphrag.agent.triage import should_escalate_fast_path
from agentic_graphrag.generation.trace import ReasoningChain


def should_escalate_chain(chain: ReasoningChain) -> bool:
    evidence_count = sum(len(step.evidence_ids) for step in chain.steps)
    has_graph = any(
        "graph" in (tool_call.tool or "") for step in chain.steps for tool_call in step.tool_calls
    )
    return should_escalate_fast_path(
        evidence_count,
        has_graph=has_graph,
        answer_status=chain.status.value if chain.status else None,
    )
