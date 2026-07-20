"""Sub-question DAG model (FR-AG-02 / P2-AG-01).

Types, normalization, topological sort, readiness, and placeholder
materialization. Offline / LLM planning logic lives in
:mod:`agentic_graphrag.agent.planner`.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

# Placeholder token: {from:sq1} or {from:sq1:entity}
PLACEHOLDER = re.compile(r"\{from:([A-Za-z0-9_]+)(?::([A-Za-z0-9_]+))?\}")


class SubQuestion(BaseModel):
    id: str
    text: str
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = ""
    # When True, ``text`` may contain ``{from:sqN}`` slots filled after deps resolve
    is_placeholder: bool = False
    status: str = Field(
        default="pending",
        description="pending|ready|running|done|skipped",
    )

    @field_validator("depends_on", mode="before")
    @classmethod
    def _coerce_deps(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return list(v)  # type: ignore[arg-type]

    def unresolved_placeholders(self) -> list[str]:
        return [m.group(1) for m in PLACEHOLDER.finditer(self.text)]


class PlanResult(BaseModel):
    sub_questions: list[SubQuestion] = Field(default_factory=list)


def normalize_plan(sub_questions: list[SubQuestion]) -> list[SubQuestion]:
    """Ensure ids, detect placeholders, topo-sort when a DAG is present."""
    by_id: dict[str, SubQuestion] = {}
    for i, sq in enumerate(sub_questions):
        sid = sq.id or f"sq{i + 1}"
        text = sq.text
        ph = bool(sq.is_placeholder or PLACEHOLDER.search(text))
        # Auto-wire depends_on from placeholder refs
        deps = list(sq.depends_on)
        for ref in PLACEHOLDER.findall(text):
            ref_id = ref[0]
            if ref_id not in deps:
                deps.append(ref_id)
        by_id[sid] = SubQuestion(
            id=sid,
            text=text,
            depends_on=deps,
            rationale=sq.rationale,
            is_placeholder=ph,
            status=sq.status or "pending",
        )
    return topological_sort(list(by_id.values()))


@dataclass
class _DepGraph:
    indeg: dict[str, int]
    children: dict[str, list[str]]


def topological_sort(sub_questions: list[SubQuestion]) -> list[SubQuestion]:
    """Kahn topo-sort; on cycle, fall back to original order."""
    ids = [sq.id for sq in sub_questions]
    by_id = {sq.id: sq for sq in sub_questions}
    graph = _build_dep_graph(sub_questions, set(ids))
    order = _kahn_order(ids, graph)
    if len(order) != len(ids):
        return sub_questions  # cycle — keep input order
    return [by_id[i] for i in order]


def _build_dep_graph(sub_questions: list[SubQuestion], id_set: set[str]) -> _DepGraph:
    indeg: dict[str, int] = {sq.id: 0 for sq in sub_questions}
    children: dict[str, list[str]] = defaultdict(list)
    graph = _DepGraph(indeg=indeg, children=children)
    for sq in sub_questions:
        _link_deps(sq, id_set, graph)
    return graph


def _link_deps(sq: SubQuestion, id_set: set[str], graph: _DepGraph) -> None:
    for d in sq.depends_on:
        if d not in id_set:
            continue
        graph.children[d].append(sq.id)
        graph.indeg[sq.id] += 1


def _kahn_order(ids: list[str], graph: _DepGraph) -> list[str]:
    q: deque[str] = deque([i for i in ids if graph.indeg[i] == 0])
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        _release_children(n, graph, q)
    return order


def _release_children(node: str, graph: _DepGraph, q: deque[str]) -> None:
    for c in graph.children[node]:
        graph.indeg[c] -= 1
        if graph.indeg[c] == 0:
            q.append(c)


def ready_subquestions(
    sub_questions: list[SubQuestion],
    done_ids: set[str],
) -> list[SubQuestion]:
    """Return pending nodes whose dependencies are all done."""
    ready: list[SubQuestion] = []
    for sq in sub_questions:
        if sq.id in done_ids or sq.status in {"done", "skipped", "running"}:
            continue
        if all(d in done_ids for d in sq.depends_on):
            ready.append(sq)
    return ready


def materialize_subquestion(
    sq: SubQuestion,
    conclusions_by_id: dict[str, str],
) -> SubQuestion:
    """Fill ``{from:sqN}`` placeholders from predecessor conclusions (P2-AG-01)."""

    def repl(match: re.Match[str]) -> str:
        ref = match.group(1)
        val = conclusions_by_id.get(ref, "").strip()
        return val if val else match.group(0)

    if not sq.is_placeholder and not PLACEHOLDER.search(sq.text):
        return sq
    new_text = PLACEHOLDER.sub(repl, sq.text)
    still_ph = bool(PLACEHOLDER.search(new_text))
    return SubQuestion(
        id=sq.id,
        text=new_text,
        depends_on=list(sq.depends_on),
        rationale=sq.rationale,
        is_placeholder=still_ph,
        status=sq.status,
    )
