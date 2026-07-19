"""Executor: choose tools and run retrieval (FR-AG-03)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentic_graphrag.agent.entities import extract_entity_mentions, is_stopword_entity
from agentic_graphrag.config import load_prompt
from agentic_graphrag.generation.trace import ToolCallTrace
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.retrieval.contracts import Candidate, concat_candidates
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.retrieval.vector import VectorRetriever


class ToolCallSpec(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ExecutorPlan(BaseModel):
    tool_calls: list[ToolCallSpec] = Field(default_factory=list)


class Executor:
    def __init__(
        self,
        graph: GraphRetriever,
        vector: VectorRetriever | None = None,
        fulltext: FulltextRetriever | None = None,
        llm: LLMProvider | None = None,
        known_entities: list[str] | None = None,
    ) -> None:
        self.graph = graph
        self.vector = vector
        self.fulltext = fulltext
        self.llm = llm
        self.known_entities = known_entities or []

    def run(
        self,
        sub_question: str,
        *,
        entities_hint: list[str] | None = None,
        allow_llm: bool = True,
    ) -> tuple[list[Candidate], list[ToolCallTrace]]:
        specs = self._choose_tools(sub_question, entities_hint or [], allow_llm=allow_llm)
        evidence: list[list[Candidate]] = []
        traces: list[ToolCallTrace] = []

        for spec in specs:
            hits = self._dispatch(spec.tool, spec.args, sub_question)
            evidence.append(hits)
            traces.append(
                ToolCallTrace(
                    tool=spec.tool,
                    reason=spec.reason,
                    args=spec.args,
                    hits=[h.id for h in hits[:20]],
                )
            )
        return concat_candidates(*evidence), traces

    def resolve_entities(self, text: str, hint: list[str] | None = None) -> list[str]:
        base = list(hint or [])
        mentions = extract_entity_mentions(text, self.known_entities or None)
        # Prefer mentions that exist in known lexicon first
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
        heuristic = self._heuristic(sub_question, entities_hint)
        if not allow_llm or self.llm is None:
            return heuristic

        try:
            prompt = load_prompt("executor")
            system, user = _split(
                prompt.format(
                    sub_question=sub_question,
                    entities_hint=", ".join(entities_hint) or "(none)",
                )
            )
            plan = complete_structured(
                self.llm,
                [Message(role="system", content=system), Message(role="user", content=user)],
                ExecutorPlan,
                tier=Tier.LIGHT,
            )
            if plan.tool_calls:
                # Sanitize entity args — never let LLM pass Who/Which as entity
                return [self._sanitize_spec(s, sub_question) for s in plan.tool_calls]
        except Exception:
            pass
        return heuristic

    def _sanitize_spec(self, spec: ToolCallSpec, sub_question: str) -> ToolCallSpec:
        args = dict(spec.args)
        names = self.resolve_entities(sub_question)
        for key in ("entity", "name", "source", "target"):
            if key in args and is_stopword_entity(str(args[key])):
                if names:
                    args[key] = (
                        names[0] if key != "target" else (names[1] if len(names) > 1 else names[0])
                    )
        return ToolCallSpec(tool=spec.tool, args=args, reason=spec.reason)

    def _heuristic(self, sub_question: str, entities_hint: list[str]) -> list[ToolCallSpec]:
        q = sub_question.lower()
        specs: list[ToolCallSpec] = []
        names = self.resolve_entities(sub_question, entities_hint)

        if (
            any(k in q for k in ("path", "between", "之间", "关系链", "connects", "chain"))
            and len(names) >= 2
        ):
            specs.append(
                ToolCallSpec(
                    tool="graph_path",
                    args={"source": names[0], "target": names[1], "max_hops": 4},
                    reason="path-style question",
                )
            )

        relation_cues = (
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
        if any(k in q for k in relation_cues):
            for ent in names[:2]:
                hops = (
                    3
                    if any(k in q for k in ("parent", "ceo of", "supplier", "compet", "chain"))
                    else 2
                )
                specs.append(
                    ToolCallSpec(
                        tool="graph_neighbors",
                        args={"entity": ent, "max_hops": hops},
                        reason=f"relation expand around {ent}",
                    )
                )

        if not specs and names:
            for ent in names[:2]:
                specs.append(
                    ToolCallSpec(
                        tool="graph_neighbors",
                        args={"entity": ent, "max_hops": 2},
                        reason="default graph expand",
                    )
                )

        # Always add lexical/semantic backup when available
        specs.append(
            ToolCallSpec(
                tool="vector_search", args={"query": sub_question}, reason="semantic recall"
            )
        )
        specs.append(
            ToolCallSpec(
                tool="fulltext_search", args={"query": sub_question}, reason="keyword recall"
            )
        )
        return specs

    def _dispatch(self, tool: str, args: dict[str, Any], sub_question: str) -> list[Candidate]:
        if tool == "graph_neighbors":
            entity = str(args.get("entity") or args.get("name") or "")
            if not entity or is_stopword_entity(entity):
                resolved = self.resolve_entities(sub_question)
                entity = resolved[0] if resolved else ""
            if not entity:
                return []
            return self.graph.neighbors(
                entity,
                max_hops=int(args.get("max_hops", 2)),
                relation_types=args.get("relation_types"),
                sub_question=sub_question,
            )
        if tool == "graph_path":
            src = str(args.get("source") or "")
            dst = str(args.get("target") or "")
            names = self.resolve_entities(sub_question)
            if not src or is_stopword_entity(src):
                src = names[0] if names else ""
            if not dst or is_stopword_entity(dst):
                dst = names[1] if len(names) > 1 else ""
            if not src or not dst:
                return []
            return self.graph.paths(
                src,
                dst,
                max_hops=int(args.get("max_hops", 4)),
                sub_question=sub_question,
            )
        if tool == "graph_subgraph":
            seeds = args.get("entities") or args.get("seeds") or []
            if isinstance(seeds, str):
                seeds = [seeds]
            if not seeds:
                seeds = self.resolve_entities(sub_question)[:3]
            return self.graph.subgraph(
                [str(s) for s in seeds],
                max_hops=int(args.get("max_hops", 2)),
                relation_types=args.get("relation_types"),
                sub_question=sub_question,
            )
        if tool == "vector_search" and self.vector is not None:
            return self.vector.search(str(args.get("query") or sub_question))
        if tool == "fulltext_search" and self.fulltext is not None:
            return self.fulltext.search(str(args.get("query") or sub_question))
        return []


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are an executor.", text


# Back-compat alias used by older tests / imports
def _extract_quoted_or_capitals(text: str) -> list[str]:
    return extract_entity_mentions(text)
