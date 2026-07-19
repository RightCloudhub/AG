"""Planner: question → sub-question DAG (FR-AG-02 / P2-AG-01).

MVP supports tree/graph dependencies and *placeholder* sub-questions whose
text is finalized only after predecessor conclusions are known
(e.g. resolve parent Y, then ask \"Who is the CEO of {Y}?\").
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

from pydantic import BaseModel, Field, field_validator

from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.config import load_prompt
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured

# Placeholder token: {from:sq1} or {from:sq1:entity}
_PLACEHOLDER = re.compile(r"\{from:([A-Za-z0-9_]+)(?::([A-Za-z0-9_]+))?\}")


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
        return [m.group(1) for m in _PLACEHOLDER.finditer(self.text)]


class PlanResult(BaseModel):
    sub_questions: list[SubQuestion] = Field(default_factory=list)


def plan(
    question: str,
    memory_summary: str,
    llm: LLMProvider | None,
    *,
    allow_llm: bool = True,
    known_entities: list[str] | None = None,
) -> list[SubQuestion]:
    if not allow_llm or llm is None:
        return plan_offline(question, known_entities=known_entities)

    prompt = load_prompt("planner")
    system, user = _split(
        prompt.format(question=question, memory_summary=memory_summary or "(empty)")
    )
    result = complete_structured(
        llm,
        [Message(role="system", content=system), Message(role="user", content=user)],
        PlanResult,
        tier=Tier.STRONG,
    )
    if not result.sub_questions:
        return plan_offline(question, known_entities=known_entities)
    return normalize_plan(result.sub_questions)


def normalize_plan(sub_questions: list[SubQuestion]) -> list[SubQuestion]:
    """Ensure ids, detect placeholders, topo-sort when a DAG is present."""
    by_id: dict[str, SubQuestion] = {}
    for i, sq in enumerate(sub_questions):
        sid = sq.id or f"sq{i + 1}"
        text = sq.text
        ph = bool(sq.is_placeholder or _PLACEHOLDER.search(text))
        # Auto-wire depends_on from placeholder refs
        deps = list(sq.depends_on)
        for ref in _PLACEHOLDER.findall(text):
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


def topological_sort(sub_questions: list[SubQuestion]) -> list[SubQuestion]:
    """Kahn topo-sort; on cycle, fall back to original order."""
    ids = [sq.id for sq in sub_questions]
    id_set = set(ids)
    by_id = {sq.id: sq for sq in sub_questions}
    indeg: dict[str, int] = {i: 0 for i in ids}
    children: dict[str, list[str]] = defaultdict(list)
    for sq in sub_questions:
        for d in sq.depends_on:
            if d not in id_set:
                continue
            children[d].append(sq.id)
            indeg[sq.id] += 1
    q: deque[str] = deque([i for i in ids if indeg[i] == 0])
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                q.append(c)
    if len(order) != len(ids):
        return sub_questions  # cycle — keep input order
    return [by_id[i] for i in order]


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

    if not sq.is_placeholder and not _PLACEHOLDER.search(sq.text):
        return sq
    new_text = _PLACEHOLDER.sub(repl, sq.text)
    still_ph = bool(_PLACEHOLDER.search(new_text))
    return SubQuestion(
        id=sq.id,
        text=new_text,
        depends_on=list(sq.depends_on),
        rationale=sq.rationale,
        is_placeholder=still_ph,
        status=sq.status,
    )


def plan_offline(
    question: str,
    *,
    known_entities: list[str] | None = None,
) -> list[SubQuestion]:
    """Deterministic multi-hop DAG decomposition without LLM."""
    q = question.strip()
    ql = q.lower()
    entities = extract_entity_mentions(q, known_entities)
    primary = entities[0] if entities else None

    # Pattern: CEO of parent/parent company of X  → tree with placeholder
    if primary and re.search(r"ceo of (the )?parent", ql):
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"What is the parent company of {primary}?",
                    rationale="multi-hop: resolve parent first",
                ),
                SubQuestion(
                    id="sq2",
                    text="Who is the CEO of {from:sq1}?",
                    depends_on=["sq1"],
                    rationale="multi-hop: CEO of resolved parent",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: companies CEO of X previously worked at
    if (
        primary
        and "ceo" in ql
        and any(k in ql for k in ("previously work", "worked at", "work at", "worked for"))
    ):
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Who is the CEO of {primary}?",
                    rationale="resolve CEO",
                ),
                SubQuestion(
                    id="sq2",
                    text="Which companies did {from:sq1} previously work at?",
                    depends_on=["sq1"],
                    rationale="prior employers of resolved CEO",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: CEO of company that competes with X
    if primary and "compet" in ql and "ceo" in ql:
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Which company competes with {primary}?",
                    rationale="find competitor",
                ),
                SubQuestion(
                    id="sq2",
                    text="Who is the CEO of {from:sq1}?",
                    depends_on=["sq1"],
                    rationale="CEO of resolved competitor",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: parent of producer of product
    if primary and "parent" in ql and any(k in ql for k in ("producer", "produce", "product")):
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Which company produces {primary}?",
                    rationale="find producer",
                ),
                SubQuestion(
                    id="sq2",
                    text="What is the parent company of {from:sq1}?",
                    depends_on=["sq1"],
                    rationale="parent of resolved producer",
                    is_placeholder=True,
                ),
            ]
        )

    # Pattern: suppliers of product that also supply competitor
    if (
        len(entities) >= 1
        and "supplier" in ql
        and any(k in ql for k in ("also", "shared", "among"))
    ):
        ent = entities[0]
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"Which companies supply or supply for {ent}?",
                    rationale="list suppliers",
                ),
                SubQuestion(
                    id="sq2",
                    text=q,
                    depends_on=["sq1"],
                    rationale="intersect suppliers",
                ),
            ]
        )

    # Pattern: shared connections between A and B (parallel fan-out → tree join)
    # Checked before generic path/connection so "shared connections" stays a DAG.
    if len(entities) >= 2 and "shared" in ql:
        a, b = entities[0], entities[1]
        return normalize_plan(
            [
                SubQuestion(id="sq1", text=f"What are neighbors of {a}?", rationale="expand A"),
                SubQuestion(id="sq2", text=f"What are neighbors of {b}?", rationale="expand B"),
                SubQuestion(
                    id="sq3",
                    text=f"What shared connections exist between {a} and {b}?",
                    depends_on=["sq1", "sq2"],
                    rationale="join neighbor sets",
                ),
            ]
        )

    # Pattern: relationship chain / path between A and B
    if len(entities) >= 2 and any(
        k in ql for k in ("relationship", "chain", "path", "connects", "between")
    ):
        a, b = entities[0], entities[1]
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"What graph path connects {a} and {b}?",
                    rationale="path query",
                ),
            ]
        )

    # Pattern: both participated in event / did A and B both...
    if "both" in ql and len(entities) >= 2:
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=f"What relations involve {entities[0]}?",
                    rationale="facts about first entity",
                ),
                SubQuestion(
                    id="sq2",
                    text=f"What relations involve {entities[1]}?",
                    rationale="facts about second entity",
                ),
                SubQuestion(
                    id="sq3",
                    text=q,
                    depends_on=["sq1", "sq2"],
                    rationale="combine both entity facts",
                ),
            ]
        )

    # Default: single hop focused on primary entity
    if primary:
        return normalize_plan(
            [
                SubQuestion(
                    id="sq1",
                    text=q,
                    rationale=f"single-hop around {primary}",
                )
            ]
        )
    return normalize_plan([SubQuestion(id="sq1", text=q, rationale="passthrough")])


def _split(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        return parts[0].replace("# System", "", 1).strip(), parts[1].strip()
    return "You are a planner.", text
