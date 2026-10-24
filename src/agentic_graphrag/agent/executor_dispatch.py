"""Tool dispatch handlers and parallel execution for Executor."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.entities import is_stopword_entity
from agentic_graphrag.agent.tools.contracts import ToolHandler
from agentic_graphrag.core.enums import RetrievalTool, ToolArgument, ToolName
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ToolCallTrace
from agentic_graphrag.retrieval.fusion import fuse_candidates

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor, ToolCallSpec

DEFAULT_NEIGHBOR_HOPS = 2
DEFAULT_PATH_HOPS = 4
DEFAULT_SUBGRAPH_HOPS = 2
MAX_SUBGRAPH_SEEDS = 3
MAX_PARALLEL_WORKERS = 8
HIT_ID_PREVIEW = 20


# ToolHandler checks positional arguments and the keyword-only tenant scope.


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
    if executor.cache is not None:
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


def handle_graph_neighbors(
    executor: Executor,
    args: dict[str, Any],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> list[Candidate]:
    entity = str(args.get(ToolArgument.ENTITY) or args.get(ToolArgument.NAME) or "")
    if not entity or is_stopword_entity(entity):
        resolved = executor.resolve_entities(sub_question)
        entity = resolved[0] if resolved else ""
    if not entity:
        return []
    return executor.graph.neighbors(
        entity,
        max_hops=int(args.get(ToolArgument.MAX_HOPS, DEFAULT_NEIGHBOR_HOPS)),
        relation_types=args.get(ToolArgument.RELATION_TYPES),
        sub_question=sub_question,
        tenant_id=tenant_id,
    )


def handle_graph_path(
    executor: Executor,
    args: dict[str, Any],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> list[Candidate]:
    src, dst = _resolve_path_endpoints(executor, args, sub_question)
    if not src or not dst:
        return []
    return executor.graph.paths(
        src,
        dst,
        max_hops=int(args.get(ToolArgument.MAX_HOPS, DEFAULT_PATH_HOPS)),
        sub_question=sub_question,
        tenant_id=tenant_id,
    )


def handle_graph_subgraph(
    executor: Executor,
    args: dict[str, Any],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> list[Candidate]:
    seeds = args.get(ToolArgument.ENTITIES) or args.get(ToolArgument.SEEDS) or []
    if isinstance(seeds, str):
        seeds = [seeds]
    if not seeds:
        seeds = executor.resolve_entities(sub_question)[:MAX_SUBGRAPH_SEEDS]
    return executor.graph.subgraph(
        [str(s) for s in seeds],
        max_hops=int(args.get(ToolArgument.MAX_HOPS, DEFAULT_SUBGRAPH_HOPS)),
        relation_types=args.get(ToolArgument.RELATION_TYPES),
        sub_question=sub_question,
        tenant_id=tenant_id,
    )


def handle_vector_search(
    executor: Executor,
    args: dict[str, Any],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> list[Candidate]:
    if executor.vector is None:
        return []
    options = (
        {ToolArgument.TOP_K: int(args[ToolArgument.TOP_K])} if ToolArgument.TOP_K in args else {}
    )
    return executor.vector.search(
        str(args.get(ToolArgument.QUERY) or sub_question), tenant_id=tenant_id, **options
    )


def handle_fulltext_search(
    executor: Executor,
    args: dict[str, Any],
    sub_question: str,
    *,
    tenant_id: str | None = None,
) -> list[Candidate]:
    if executor.fulltext is None:
        return []
    return executor.fulltext.search(
        str(args.get(ToolArgument.QUERY) or sub_question), tenant_id=tenant_id
    )


TOOL_HANDLERS: dict[RetrievalTool, ToolHandler] = {
    RetrievalTool.GRAPH_NEIGHBORS: handle_graph_neighbors,
    RetrievalTool.GRAPH_PATH: handle_graph_path,
    RetrievalTool.GRAPH_SUBGRAPH: handle_graph_subgraph,
    RetrievalTool.VECTOR_SEARCH: handle_vector_search,
    RetrievalTool.FULLTEXT_SEARCH: handle_fulltext_search,
}


def _resolve_path_endpoints(
    executor: Executor, args: dict[str, Any], sub_question: str
) -> tuple[str, str]:
    src = str(args.get(ToolArgument.SOURCE) or "")
    dst = str(args.get(ToolArgument.TARGET) or "")
    names = executor.resolve_entities(sub_question)
    if not src or is_stopword_entity(src):
        src = names[0] if names else ""
    if not dst or is_stopword_entity(dst):
        dst = names[1] if len(names) > 1 else ""
    return src, dst


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
            pool.submit(
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
