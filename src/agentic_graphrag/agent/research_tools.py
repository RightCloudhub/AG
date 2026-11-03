"""Bounded retrieval through ToolRegistry with explicit tenant identity."""

from copy import copy
from dataclasses import replace
from functools import partial

from agentic_graphrag.agent.executor import Executor, ToolCallSpec
from agentic_graphrag.agent.executor_dispatch import _record_tool_failure, dispatch
from agentic_graphrag.agent.research_cache import QueryCache, QueryDedup, scoped_key
from agentic_graphrag.agent.research_state import ResearchTask
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ToolCallTrace
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.llm.scoped import scoped_client
from agentic_graphrag.retrieval.fusion import fuse_candidates


class EvidenceScopeError(ValueError):
    """A storage scope violation must never become an empty successful result."""


def branch_executor(executor: Executor, task: ResearchTask, budget: BudgetTracker) -> Executor:
    local = copy(executor)
    local.deps = replace(executor.deps, tenant_id=task.tenant_id)
    local.cache = None
    if executor.vector is not None:
        local.vector = copy(executor.vector)
        local.vector.llm = scoped_client(executor.vector.llm, budget, max_output_tokens=1)
    return local


def retrieve(
    executor: Executor,
    task: ResearchTask,
    cache: QueryDedup,
    *,
    query: str,
    limit: int,
) -> tuple[dict[str, Candidate], list[ToolCallTrace], QueryCache]:
    specs = executor._choose_tools(query, task.requirement.anchors, allow_llm=False)[:limit]
    groups: list[list[Candidate]] = []
    traces: list[ToolCallTrace] = []
    saved: QueryCache = {}
    for spec in specs:
        key = scoped_key(task.tenant_id, task.run_id, tool=str(spec.tool), args=spec.args)
        try:
            hits = cache.retrieve(key, partial(_invoke, executor, task, spec, query=query))
        except (EvidenceScopeError, BudgetExceeded):
            raise
        except Exception as exc:
            # Match the existing executor's channel degradation policy. A failed
            # channel is observable and retryable, while healthy tools still run.
            traces.append(
                ToolCallTrace(
                    tool=spec.tool, args=spec.args, reason=f"(degraded: {type(exc).__name__})"
                )
            )
            continue
        saved[key] = hits
        groups.append(hits)
        traces.append(ToolCallTrace(tool=spec.tool, args=spec.args, hits=[hit.id for hit in hits]))
    fused = fuse_candidates(
        *groups,
        query=query,
        method=executor.fusion_method,
        k=executor.fusion_k,
        limit=executor.fusion_limit,
        reranker=executor.reranker,
    )
    return {hit.id: hit for hit in fused}, traces, saved


def _invoke(
    executor: Executor, task: ResearchTask, spec: ToolCallSpec, *, query: str
) -> list[Candidate]:
    try:
        hits = dispatch(
            executor, spec.tool, spec.args, sub_question=query, tenant_id=task.tenant_id
        )
        for hit in hits:
            owner = hit.metadata.get("tenant_id")
            if owner is not None and str(owner) != task.tenant_id:
                raise EvidenceScopeError("retrieval returned evidence for a different tenant")
        return hits
    except Exception as exc:
        _record_tool_failure(executor, spec.tool, exc)
        raise
