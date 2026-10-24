"""Per-run tool deduplication, preserving the existing retrievers and fusion."""

from __future__ import annotations

import json

from agentic_graphrag.agent.executor import Executor, ToolCallSpec
from agentic_graphrag.agent.executor_dispatch import run_tool_specs
from agentic_graphrag.core.enums import RetrievalTool
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.reasoning import ToolCallTrace
from agentic_graphrag.retrieval.fusion import fuse_candidates


def query_key(tool: str, args: JsonObject) -> str:
    normalized = dict(args)
    if "query" in normalized:
        query = " ".join(str(normalized["query"]).casefold().split())
        normalized["query"] = (
            " ".join(sorted(query.split())) if tool == RetrievalTool.FULLTEXT_SEARCH else query
        )
    return tool + ":" + json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def search(
    executor: Executor,
    query: str,
    cache: dict[str, list[str]],
    *,
    tenant_id: str,
    anchors: list[str],
) -> tuple[list[Candidate], list[ToolCallTrace]]:
    specs = executor._choose_tools(query, anchors, allow_llm=False)
    pending, reused = _uncached(specs, cache)
    groups, traces = run_tool_specs(executor, pending, query, tenant_id=tenant_id)
    for spec, trace in zip(pending, traces, strict=True):
        # Failed calls remain retryable; successful empty results are cached.
        if "(degraded:" not in trace.reason:
            cache[query_key(spec.tool, spec.args)] = list(trace.hits)
    fused = fuse_candidates(
        *groups,
        query=query,
        method=executor.fusion_method,
        k=executor.fusion_k,
        limit=executor.fusion_limit,
        reranker=executor.reranker,
    )
    return fused, traces + reused


def seed_search(
    executor: Executor, question: str, *, top_k: int, tenant_id: str, cache: dict[str, list[str]]
) -> tuple[list[Candidate], ToolCallTrace]:
    spec = ToolCallSpec(
        tool=RetrievalTool.VECTOR_SEARCH,
        args={"query": question, "top_k": top_k},
        reason="requirement register seed",
    )
    groups, traces = run_tool_specs(executor, [spec], question, tenant_id=tenant_id)
    trace = traces[0]
    if "(degraded:" not in trace.reason:
        cache[query_key(spec.tool, spec.args)] = list(trace.hits)
    return groups[0], trace


def _uncached(
    specs: list[ToolCallSpec], cache: dict[str, list[str]]
) -> tuple[list[ToolCallSpec], list[ToolCallTrace]]:
    pending, reused = [], []
    keys: set[str] = set()
    for spec in specs:
        key = query_key(spec.tool, spec.args)
        if key in cache or key in keys:
            reused.append(
                ToolCallTrace(
                    tool=RetrievalTool.CACHE,
                    args={"tool": spec.tool, **spec.args},
                    reason="duplicate query; revise the need or read evidence",
                    hits=cache.get(key, []),
                )
            )
        else:
            pending.append(spec)
            keys.add(key)
    return pending, reused
