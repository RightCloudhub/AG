"""Executor: choose tools and run retrieval (FR-AG-03 / P3-PERF-02/03)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from agentic_graphrag.agent.entities import extract_entity_mentions, is_stopword_entity
from agentic_graphrag.agent.executor_dispatch import (
    cache_hit_result,
    dispatch,
    fuse_and_cache,
    run_tool_specs,
)
from agentic_graphrag.agent.executor_plan import (
    build_heuristic,
    choose_tools,
    sanitize_spec,
    split_prompt,
)
from agentic_graphrag.generation.trace import ToolCallTrace
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.retrieval.cache import RetrievalCache
from agentic_graphrag.retrieval.contracts import Candidate
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.fusion import Reranker
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.vector import VectorRetriever

DEFAULT_FUSION_METHOD = "rrf"
DEFAULT_FUSION_K = 60
DEFAULT_FUSION_LIMIT = 30

__all__ = [
    "Executor",
    "ExecutorConfig",
    "ExecutorDeps",
    "ExecutorPlan",
    "ToolCallSpec",
    "_extract_quoted_or_capitals",
    "_split",
]


class ToolCallSpec(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ExecutorPlan(BaseModel):
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)


@dataclass
class ExecutorDeps:
    """Optional retrieval/LLM dependencies for Executor."""

    vector: VectorRetriever | None = None
    fulltext: FulltextRetriever | None = None
    llm: LLMProvider | None = None
    known_entities: list[str] | None = None


@dataclass
class ExecutorConfig:
    """Runtime options for parallel retrieval and fusion."""

    parallel: bool = True
    fusion_method: str = DEFAULT_FUSION_METHOD
    fusion_k: int = DEFAULT_FUSION_K
    fusion_limit: int | None = DEFAULT_FUSION_LIMIT
    cache: RetrievalCache | None = None
    reranker: Reranker | None = None


class Executor:
    """Choose tools and retrieve evidence for a sub-question.

    Preferred construction::

        Executor(graph, deps=ExecutorDeps(...), config=ExecutorConfig(...))

    Flat keyword args remain supported for call-site compatibility.
    """

    def __init__(
        self,
        graph: GraphRetriever,
        deps: ExecutorDeps | None = None,
        config: ExecutorConfig | None = None,
        *,
        vector: VectorRetriever | None = None,
        fulltext: FulltextRetriever | None = None,
        llm: LLMProvider | None = None,
        known_entities: list[str] | None = None,
        parallel: bool | None = None,
        fusion_method: str | None = None,
        fusion_k: int | None = None,
        fusion_limit: int | None = None,
        cache: RetrievalCache | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.graph = graph
        self.deps = deps or ExecutorDeps(
            vector=vector,
            fulltext=fulltext,
            llm=llm,
            known_entities=known_entities,
        )
        self.config = _merge_config(
            config,
            parallel=parallel,
            fusion_method=fusion_method,
            fusion_k=fusion_k,
            fusion_limit=fusion_limit,
            cache=cache,
            reranker=reranker,
        )
        self.vector = self.deps.vector
        self.fulltext = self.deps.fulltext
        self.llm = self.deps.llm
        self.known_entities = self.deps.known_entities or []
        self.parallel = self.config.parallel
        self.fusion_method = self.config.fusion_method
        self.fusion_k = self.config.fusion_k
        self.fusion_limit = self.config.fusion_limit
        self.cache = self.config.cache
        self.reranker = self.config.reranker

    def run(
        self,
        sub_question: str,
        *,
        entities_hint: list[str] | None = None,
        allow_llm: bool = True,
    ) -> tuple[list[Candidate], list[ToolCallTrace]]:
        specs = self._choose_tools(sub_question, entities_hint or [], allow_llm=allow_llm)
        tools_key = ",".join(sorted(s.tool for s in specs))
        if self.cache is not None:
            cached = self.cache.get_retrieval(sub_question, tools_key)
            if cached is not None:
                return cache_hit_result(cached, tools_key)

        evidence, traces = run_tool_specs(self, specs, sub_question)
        fused = fuse_and_cache(self, evidence, sub_question, tools_key=tools_key)
        return fused, traces

    def resolve_entities(self, text: str, hint: list[str] | None = None) -> list[str]:
        base = list(hint or [])
        mentions = extract_entity_mentions(text, self.known_entities or None)
        ordered: list[str] = []
        seen: set[str] = set()
        for name in base + mentions:
            if is_stopword_entity(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(name)
        return ordered

    def _choose_tools(
        self,
        sub_question: str,
        entities_hint: list[str],
        *,
        allow_llm: bool,
    ) -> list[ToolCallSpec]:
        return choose_tools(self, sub_question, entities_hint, allow_llm=allow_llm)

    def _sanitize_spec(self, spec: ToolCallSpec, sub_question: str) -> ToolCallSpec:
        return sanitize_spec(self, spec, sub_question)

    def _heuristic(self, sub_question: str, entities_hint: list[str]) -> list[ToolCallSpec]:
        return build_heuristic(self, sub_question, entities_hint)

    def _dispatch(self, tool: str, args: dict[str, Any], sub_question: str) -> list[Candidate]:
        return dispatch(self, tool, args, sub_question=sub_question)

    def _run_parallel(
        self, specs: list[ToolCallSpec], sub_question: str
    ) -> list[tuple[ToolCallSpec, list[Candidate], str | None]]:
        from agentic_graphrag.agent.executor_dispatch import _run_parallel

        return _run_parallel(self, specs, sub_question)


def _merge_config(
    config: ExecutorConfig | None,
    *,
    parallel: bool | None,
    fusion_method: str | None,
    fusion_k: int | None,
    fusion_limit: int | None,
    cache: RetrievalCache | None,
    reranker: Reranker | None,
) -> ExecutorConfig:
    base = config or ExecutorConfig()
    return ExecutorConfig(
        parallel=base.parallel if parallel is None else parallel,
        fusion_method=base.fusion_method if fusion_method is None else fusion_method,
        fusion_k=base.fusion_k if fusion_k is None else fusion_k,
        fusion_limit=base.fusion_limit if fusion_limit is None else fusion_limit,
        cache=base.cache if cache is None else cache,
        reranker=base.reranker if reranker is None else reranker,
    )


def _split(text: str) -> tuple[str, str]:
    return split_prompt(text)


def _extract_quoted_or_capitals(text: str) -> list[str]:
    """Back-compat alias used by older tests / imports."""
    return extract_entity_mentions(text)
