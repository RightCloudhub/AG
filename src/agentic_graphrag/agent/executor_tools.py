"""Built-in retrieval tool handlers and the tool → handler map.

Split out of :mod:`agentic_graphrag.agent.executor_dispatch` (300-line gate): the
dispatch/fan-out loop stays there, the handlers live here. Every handler takes the
executor, the tool arguments, the sub-question and the tenant scope, and returns
retrieval candidates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.entities import is_stopword_entity
from agentic_graphrag.agent.tools.contracts import ToolHandler
from agentic_graphrag.core.enums import RetrievalTool, ToolArgument
from agentic_graphrag.core.evidence import Candidate

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor

DEFAULT_NEIGHBOR_HOPS = 2
DEFAULT_PATH_HOPS = 4
DEFAULT_SUBGRAPH_HOPS = 2
MAX_SUBGRAPH_SEEDS = 3


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
        limit=executor.config.candidate_pool,
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
        limit=executor.config.candidate_pool,
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
        limit=executor.config.candidate_pool,
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
    if executor.config.candidate_pool is not None:
        options[ToolArgument.TOP_K] = executor.config.candidate_pool
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
