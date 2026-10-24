"""Tool selection: heuristic plans, LLM plans, and arg sanitization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.agent.entities import is_stopword_entity
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.constants import PROMPT_SYSTEM_HEADER, PROMPT_USER_HEADER
from agentic_graphrag.core.enums import (
    GRAPH_TOOLS,
    MessageRole,
    PromptName,
    RetrievalTool,
    ToolArgument,
)
from agentic_graphrag.core.ports.llm import Message, Tier
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
    "收购",
    "被收购",
    "总部",
    "控股",
    "旗下",
    "首席执行官",
)
LONG_HOP_CUES = (
    "parent",
    "ceo of",
    "supplier",
    "compet",
    "chain",
    "acquir",
    "收购",
    "总部",
    "母公司",
)
DEFAULT_NEIGHBOR_HOPS = 2
LONG_NEIGHBOR_HOPS = 3
PATH_MAX_HOPS = 4
MAX_NAMED_ENTITIES = 2
ENTITY_ARG_KEYS = (ToolArgument.ENTITY, ToolArgument.NAME, ToolArgument.SOURCE, ToolArgument.TARGET)


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
    if any(s.tool in GRAPH_TOOLS for s in heuristic):
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
    has_graph = any(s.tool in GRAPH_TOOLS for s in specs)
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
    if PROMPT_SYSTEM_HEADER in text and PROMPT_USER_HEADER in text:
        parts = text.split(PROMPT_USER_HEADER, 1)
        return parts[0].replace(PROMPT_SYSTEM_HEADER, "", 1).strip(), parts[1].strip()
    return "You are an executor.", text


def _replacement_name(key: str, names: list[str]) -> str:
    if key != ToolArgument.TARGET:
        return names[0]
    return names[1] if len(names) > 1 else names[0]


def _path_specs(q: str, names: list[str]) -> list[ToolCallSpec]:
    from agentic_graphrag.agent.executor import ToolCallSpec

    if not (any(k in q for k in PATH_CUES) and len(names) >= 2):
        return []
    return [
        ToolCallSpec(
            tool=RetrievalTool.GRAPH_PATH,
            args={
                ToolArgument.SOURCE: names[0],
                ToolArgument.TARGET: names[1],
                ToolArgument.MAX_HOPS: PATH_MAX_HOPS,
            },
            reason="path-style question",
        )
    ]


def _relation_specs(q: str, names: list[str]) -> list[ToolCallSpec]:
    from agentic_graphrag.agent.executor import ToolCallSpec

    if not any(k in q for k in RELATION_CUES):
        return []
    hops = LONG_NEIGHBOR_HOPS if any(k in q for k in LONG_HOP_CUES) else DEFAULT_NEIGHBOR_HOPS
    return [
        ToolCallSpec(
            tool=RetrievalTool.GRAPH_NEIGHBORS,
            args={ToolArgument.ENTITY: ent, ToolArgument.MAX_HOPS: hops},
            reason=f"relation expand around {ent}",
        )
        for ent in names[:MAX_NAMED_ENTITIES]
    ]


def _default_neighbor_specs(names: list[str]) -> list[ToolCallSpec]:
    from agentic_graphrag.agent.executor import ToolCallSpec

    return [
        ToolCallSpec(
            tool=RetrievalTool.GRAPH_NEIGHBORS,
            args={ToolArgument.ENTITY: ent, ToolArgument.MAX_HOPS: DEFAULT_NEIGHBOR_HOPS},
            reason="default graph expand",
        )
        for ent in names[:MAX_NAMED_ENTITIES]
    ]


def _lexical_backup_specs(sub_question: str, *, skip_vector: bool = False) -> list[ToolCallSpec]:
    """Fulltext always; vector optional (remote embed is a major live P95 cost)."""
    from agentic_graphrag.agent.executor import ToolCallSpec

    specs: list[ToolCallSpec] = [
        ToolCallSpec(
            tool=RetrievalTool.FULLTEXT_SEARCH,
            args={ToolArgument.QUERY: sub_question},
            reason="keyword recall",
        ),
    ]
    if not skip_vector:
        specs.insert(
            0,
            ToolCallSpec(
                tool=RetrievalTool.VECTOR_SEARCH,
                args={ToolArgument.QUERY: sub_question},
                reason="semantic recall",
            ),
        )
    return specs


def _try_llm_plan(
    executor: Executor, sub_question: str, entities_hint: list[str]
) -> list[ToolCallSpec] | None:
    from agentic_graphrag.agent.executor import ExecutorPlan

    if executor.llm is None:
        return None
    try:
        prompt = load_prompt(PromptName.EXECUTOR)
        system, user = split_prompt(
            prompt.format(
                sub_question=sub_question,
                entities_hint=", ".join(entities_hint) or "(none)",
            )
        )
        plan = complete_structured(
            executor.llm,
            [
                Message(role=MessageRole.SYSTEM, content=system),
                Message(role=MessageRole.USER, content=user),
            ],
            ExecutorPlan,
            tier=Tier.LIGHT,
        )
        if plan.tool_calls:
            return list(plan.tool_calls)
    except Exception:
        return None
    return None
