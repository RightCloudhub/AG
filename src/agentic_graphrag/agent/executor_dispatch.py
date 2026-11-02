"""Tool dispatch handlers and parallel execution for Executor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from agentic_graphrag.core.enums import RetrievalChannel, RetrievalTool, ToolName
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ToolCallTrace
from agentic_graphrag.observability.context import submit_with_context
from agentic_graphrag.observability.degradation import (
    REASON_TOOL_FAILED,
    ChannelStatus,
    record_degradation,
)
from agentic_graphrag.retrieval.fusion import fuse_candidates

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor, ToolCallSpec

DEFAULT_NEIGHBOR_HOPS = 2
DEFAULT_PATH_HOPS = 4
DEFAULT_SUBGRAPH_HOPS = 2
MAX_SUBGRAPH_SEEDS = 3
MAX_PARALLEL_WORKERS = 8
HIT_ID_PREVIEW = 20

# Which channel a failed tool call belongs to (O-9). Keyed by the tool's string
# value so an external tool name simply misses.
_TOOL_CHANNELS: dict[str, RetrievalChannel] = {
    RetrievalTool.VECTOR_SEARCH: RetrievalChannel.VECTOR,
    RetrievalTool.FULLTEXT_SEARCH: RetrievalChannel.FULLTEXT,
    RetrievalTool.GRAPH_NEIGHBORS: RetrievalChannel.GRAPH,
    RetrievalTool.GRAPH_PATH: RetrievalChannel.GRAPH,
    RetrievalTool.GRAPH_SUBGRAPH: RetrievalChannel.GRAPH,
}


def _record_tool_failure(executor: Executor, tool: ToolName, exc: BaseException) -> None:
    """Attribute a swallowed tool failure to its channel.

    Without this a channel that returned nothing because it raised looks the same
    as a channel that legitimately had no evidence.
    """
    channel = _TOOL_CHANNELS.get(str(tool))
    if channel is None:
        return
    record_degradation(
        channel,
        REASON_TOOL_FAILED,
        exc,
        severity=ChannelStatus.FAILED,
        health=executor.health,
    )


def dispatch(
    executor: Executor,
    tool: ToolName,
    args: dict[str, Any],
    *,
    sub_question: str,
    tenant_id: str | None = None,
) -> list[Candidate]:
    """Route a tool call through the executor's registry (single dispatch point)."""
    registry = executor.tools
    if not registry.has(tool):
        return []
    return registry.invoke(
        tool, executor=executor, args=args, sub_question=sub_question, tenant_id=tenant_id
    )


def run_tool_specs(
    executor: Executor,
    specs: list[ToolCallSpec],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> tuple[list[list[Candidate]], list[ToolCallTrace]]:
    if executor.parallel and len(specs) > 1:
        return _collect_parallel(executor, specs, sub_question, tenant_id=tenant_id)
    return _collect_sequential(executor, specs, sub_question, tenant_id=tenant_id)


def fuse_and_cache(
    executor: Executor,
    evidence: list[list[Candidate]],
    sub_question: str,
    *,
    tools_key: str,
    tenant_id: str | None = None,
) -> list[Candidate]:
    fused = fuse_candidates(
        *evidence,
        query=sub_question,
        method=executor.fusion_method,
        k=executor.fusion_k,
        limit=executor.fusion_limit,
        reranker=executor.reranker,
    )
    # Check after reranking, which can also degrade. Health is shared across the
    # run: never cache partial/fallback evidence as healthy for the next query.
    if executor.cache is not None and not executor.health.is_degraded():
        executor.cache.set_retrieval(sub_question, fused, tools_key, tenant_id=tenant_id)
    return fused


def cache_hit_result(
    cached: list[Candidate], tools_key: str
) -> tuple[list[Candidate], list[ToolCallTrace]]:
    traces = [
        ToolCallTrace(
            tool=RetrievalTool.CACHE,
            reason="retrieval cache hit",
            args={"tools": tools_key},
            hits=[h.id for h in cached[:HIT_ID_PREVIEW]],
        )
    ]
    return cached, traces


def _collect_sequential(
    executor: Executor,
    specs: list[ToolCallSpec],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> tuple[list[list[Candidate]], list[ToolCallTrace]]:
    evidence: list[list[Candidate]] = []
    traces: list[ToolCallTrace] = []
    for spec in specs:
        hits, err = _safe_dispatch(executor, spec, sub_question, tenant_id=tenant_id)
        evidence.append(hits)
        traces.append(_trace_for(spec, hits, err))
    return evidence, traces


def _collect_parallel(
    executor: Executor,
    specs: list[ToolCallSpec],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> tuple[list[list[Candidate]], list[ToolCallTrace]]:
    rows = _run_parallel(executor, specs, sub_question, tenant_id=tenant_id)
    evidence: list[list[Candidate]] = []
    traces: list[ToolCallTrace] = []
    for spec, hits, err in rows:
        evidence.append(hits)
        traces.append(_trace_for(spec, hits, err))
    return evidence, traces


def _run_parallel(
    executor: Executor,
    specs: list[ToolCallSpec],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> list[tuple[ToolCallSpec, list[Candidate], str | None]]:
    out: list[tuple[ToolCallSpec, list[Candidate], str | None]] = []
    workers = min(MAX_PARALLEL_WORKERS, len(specs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            submit_with_context(
                pool,
                dispatch,
                executor,
                spec.tool,
                spec.args,
                sub_question=sub_question,
                tenant_id=tenant_id,
            ): spec
            for spec in specs
        }
        for fut in as_completed(futs):
            spec = futs[fut]
            try:
                out.append((spec, fut.result(), None))
            except Exception as exc:  # noqa: BLE001
                _record_tool_failure(executor, spec.tool, exc)
                out.append((spec, [], type(exc).__name__))
    order = {id(s): i for i, s in enumerate(specs)}
    out.sort(key=lambda row: order.get(id(row[0]), 0))
    return out


def _safe_dispatch(
    executor: Executor,
    spec: ToolCallSpec,
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> tuple[list[Candidate], str | None]:
    try:
        hits = dispatch(
            executor, spec.tool, spec.args, sub_question=sub_question, tenant_id=tenant_id
        )
        return hits, None
    except Exception as exc:  # noqa: BLE001 — channel failure degrades
        _record_tool_failure(executor, spec.tool, exc)
        return [], type(exc).__name__


def _trace_for(spec: ToolCallSpec, hits: list[Candidate], err: str | None) -> ToolCallTrace:
    reason = spec.reason
    if err:
        reason = f"{spec.reason} (degraded: {err})"
    return ToolCallTrace(
        tool=spec.tool,
        reason=reason,
        args=spec.args,
        hits=[h.id for h in hits[:HIT_ID_PREVIEW]],
    )
