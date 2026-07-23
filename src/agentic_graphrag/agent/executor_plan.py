"""Tool selection: heuristic plans, LLM plans, and arg sanitization."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentic_graphrag.agent.entities import is_stopword_entity
from agentic_graphrag.config import load_prompt
from agentic_graphrag.llm.provider import Message, Tier
from agentic_graphrag.llm.structured import complete_structured

if TYPE_CHECKING:
    from agentic_graphrag.agent.executor import Executor, ToolCallSpec

PATH_CUES = ("path", "between", "之间", "关系链", "connects", "chain")
RELATION_CUES = (
    "ceo",
    "母公司",
    "parent",
    "subsidiary",
    "work",
    "任职",
    "supplier",
    "供应",
    "produce",
    "compet",
    "own",
    "neighbor",
    "relation",
    "participat",
    "partner",
    "acquir",
)
LONG_HOP_CUES = ("parent", "ceo of", "supplier", "compet", "chain")
DEFAULT_NEIGHBOR_HOPS = 2
LONG_NEIGHBOR_HOPS = 3
PATH_MAX_HOPS = 4
MAX_NAMED_ENTITIES = 2
ENTITY_ARG_KEYS = ("entity", "name", "source", "target")


def choose_tools(
    executor: Executor,
    sub_question: str,
    entities_hint: list[str],
    *,
    allow_llm: bool,
) -> list[ToolCallSpec]:
    """Pick tools. Prefer heuristic when graph already targets entities (P95).

    Remote LLM tool-planning is expensive (seconds–tens of seconds). Use it only
    when heuristics have no graph/path handle — not on every Fast Path hop.
    """
    heuristic = build_heuristic(executor, sub_question, entities_hint)
    if not allow_llm or executor.llm is None:
        return heuristic
    # Strong signal already: graph expand / path — skip extra LLM plan call.
    if any(s.tool in {"graph_neighbors", "graph_path", "graph_subgraph"} for s in heuristic):
        return heuristic
    llm_plan = _try_llm_plan(executor, sub_question, entities_hint)
    if llm_plan:
        return [sanitize_spec(executor, s, sub_question) for s in llm_plan]
    return heuristic


def build_heuristic(
    executor: Executor, sub_question: str, entities_hint: list[str]
) -> list[ToolCallSpec]:

    q = sub_question.lower()
    specs: list[ToolCallSpec] = []
    names = executor.resolve_entities(sub_question, entities_hint)
    specs.extend(_path_specs(q, names))
    specs.extend(_relation_specs(q, names))
    if not specs and names:
        specs.extend(_default_neighbor_specs(names))
    # Skip remote embedding when graph already expanded (AC-4 latency).
    has_graph = any(s.tool.startswith("graph_") for s in specs)
    specs.extend(_lexical_backup_specs(sub_question, skip_vector=has_graph))
    return specs


def sanitize_spec(executor: Executor, spec: ToolCallSpec, sub_question: str) -> ToolCallSpec:
    from agentic_graphrag.agent.executor import ToolCallSpec

    args = dict(spec.args)
    names = executor.resolve_entities(sub_question)
    for key in ENTITY_ARG_KEYS:
        if key not in args or not is_stopword_entity(str(args[key])):
            continue
        if not names:
            continue
        args[key] = _replacement_name(key, names)
    return ToolCallSpec(tool=spec.tool, args=args, reason=spec.reason)


def split_prompt(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are an executor.", text


def _replacement_name(key: str, names: list[str]) -> str:
    if key != "target":
        return names[0]
    return names[1] if len(names) > 1 else names[0]


def _path_specs(q: str, names: list[str]) -> list[Any]:
    from agentic_graphrag.agent.executor import ToolCallSpec

    if not (any(k in q for k in PATH_CUES) and len(names) >= 2):
        return []
    return [
        ToolCallSpec(
            tool="graph_path",
            args={"source": names[0], "target": names[1], "max_hops": PATH_MAX_HOPS},
            reason="path-style question",
        )
    ]


def _relation_specs(q: str, names: list[str]) -> list[Any]:
    from agentic_graphrag.agent.executor import ToolCallSpec

    if not any(k in q for k in RELATION_CUES):
        return []
    hops = LONG_NEIGHBOR_HOPS if any(k in q for k in LONG_HOP_CUES) else DEFAULT_NEIGHBOR_HOPS
    return [
        ToolCallSpec(
            tool="graph_neighbors",
            args={"entity": ent, "max_hops": hops},
            reason=f"relation expand around {ent}",
        )
        for ent in names[:MAX_NAMED_ENTITIES]
    ]


def _default_neighbor_specs(names: list[str]) -> list[Any]:
    from agentic_graphrag.agent.executor import ToolCallSpec

    return [
        ToolCallSpec(
            tool="graph_neighbors",
            args={"entity": ent, "max_hops": DEFAULT_NEIGHBOR_HOPS},
            reason="default graph expand",
        )
        for ent in names[:MAX_NAMED_ENTITIES]
    ]


def _lexical_backup_specs(sub_question: str, *, skip_vector: bool = False) -> list[Any]:
    """Fulltext always; vector optional (remote embed is a major live P95 cost)."""
    from agentic_graphrag.agent.executor import ToolCallSpec

    specs: list[Any] = [
        ToolCallSpec(tool="fulltext_search", args={"query": sub_question}, reason="keyword recall"),
    ]
    if not skip_vector:
        specs.insert(
            0,
            ToolCallSpec(
                tool="vector_search",
                args={"query": sub_question},
                reason="semantic recall",
            ),
        )
    return specs


def _try_llm_plan(
    executor: Executor, sub_question: str, entities_hint: list[str]
) -> list[ToolCallSpec] | None:
    from agentic_graphrag.agent.executor import ExecutorPlan

    try:
        prompt = load_prompt("executor")
        system, user = split_prompt(
            prompt.format(
                sub_question=sub_question,
                entities_hint=", ".join(entities_hint) or "(none)",
            )
        )
        plan = complete_structured(
            executor.llm,
            [Message(role="system", content=system), Message(role="user", content=user)],
            ExecutorPlan,
            tier=Tier.LIGHT,
        )
        if plan.tool_calls:
            return list(plan.tool_calls)
    except Exception:
        return None
    return None
