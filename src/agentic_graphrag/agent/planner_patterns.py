"""Offline plan_offline pattern matchers (each returns list[SubQuestion] | None)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from agentic_graphrag.agent.plan_dag import SubQuestion

# Relation / multi-hop cue constants (no magic strings scattered in matchers)
_CEO = "ceo"
_PARENT = "parent"
_COMPET = "compet"
_SUPPLIER = "supplier"
_SHARED = "shared"
_BOTH = "both"
_WORK_CUES = ("previously work", "worked at", "work at", "worked for")
_PRODUCT_CUES = ("producer", "produce", "product")
_SUPPLY_JOIN_CUES = ("also", "shared", "among")
_PATH_CUES = ("relationship", "chain", "path", "connects", "between")
_CEO_PARENT_RE = re.compile(r"ceo of (the )?parent")


@dataclass(frozen=True)
class PlanContext:
    """Normalized offline planning inputs."""

    q: str
    ql: str
    entities: list[str]
    primary: str | None


Matcher = Callable[[PlanContext], list[SubQuestion] | None]


@dataclass(frozen=True)
class _TwoHop:
    sq1_text: str
    sq1_rationale: str
    sq2_text: str
    sq2_rationale: str
    placeholder: bool = True


def _two_hop(spec: _TwoHop) -> list[SubQuestion]:
    return [
        SubQuestion(id="sq1", text=spec.sq1_text, rationale=spec.sq1_rationale),
        SubQuestion(
            id="sq2",
            text=spec.sq2_text,
            depends_on=["sq1"],
            rationale=spec.sq2_rationale,
            is_placeholder=spec.placeholder,
        ),
    ]


def _three_hop(
    sq1: tuple[str, str],
    sq2: tuple[str, str],
    sq3: tuple[str, str],
) -> list[SubQuestion]:
    return [
        SubQuestion(id="sq1", text=sq1[0], rationale=sq1[1]),
        SubQuestion(
            id="sq2",
            text=sq2[0],
            depends_on=["sq1"],
            rationale=sq2[1],
            is_placeholder=True,
        ),
        SubQuestion(
            id="sq3",
            text=sq3[0],
            depends_on=["sq2"],
            rationale=sq3[1],
            is_placeholder=True,
        ),
    ]


def match_ceo_of_parent(ctx: PlanContext) -> list[SubQuestion] | None:
    if not ctx.primary or not _CEO_PARENT_RE.search(ctx.ql):
        return None
    return _two_hop(
        _TwoHop(
            f"What is the parent company of {ctx.primary}?",
            "multi-hop: resolve parent first",
            "Who is the CEO of {from:sq1}?",
            "multi-hop: CEO of resolved parent",
        )
    )


def match_ceo_previous_work(ctx: PlanContext) -> list[SubQuestion] | None:
    if not ctx.primary or _CEO not in ctx.ql:
        return None
    if not any(k in ctx.ql for k in _WORK_CUES):
        return None
    return _two_hop(
        _TwoHop(
            f"Who is the CEO of {ctx.primary}?",
            "resolve CEO",
            "Which companies did {from:sq1} previously work at?",
            "prior employers of resolved CEO",
        )
    )


def match_ceo_of_competitor_of_producer(ctx: PlanContext) -> list[SubQuestion] | None:
    """CEO of competitor of producer of product (3-hop)."""
    if not ctx.primary or _COMPET not in ctx.ql or _CEO not in ctx.ql:
        return None
    if not any(k in ctx.ql for k in _PRODUCT_CUES):
        return None
    return _three_hop(
        (f"Which company produces {ctx.primary}?", "find producer"),
        ("Which company competes with {from:sq1}?", "find competitor of producer"),
        ("Who is the CEO of {from:sq2}?", "CEO of resolved competitor"),
    )


def match_ceo_of_competitor(ctx: PlanContext) -> list[SubQuestion] | None:
    if not ctx.primary or _COMPET not in ctx.ql or _CEO not in ctx.ql:
        return None
    # Product→producer chain handled by match_ceo_of_competitor_of_producer.
    if any(k in ctx.ql for k in _PRODUCT_CUES):
        return None
    return _two_hop(
        _TwoHop(
            f"Which company competes with {ctx.primary}?",
            "find competitor",
            "Who is the CEO of {from:sq1}?",
            "CEO of resolved competitor",
        )
    )


def match_parent_of_producer(ctx: PlanContext) -> list[SubQuestion] | None:
    if not ctx.primary or _PARENT not in ctx.ql:
        return None
    if not any(k in ctx.ql for k in _PRODUCT_CUES):
        return None
    return _two_hop(
        _TwoHop(
            f"Which company produces {ctx.primary}?",
            "find producer",
            "What is the parent company of {from:sq1}?",
            "parent of resolved producer",
        )
    )


def match_suppliers_intersect(ctx: PlanContext) -> list[SubQuestion] | None:
    if not ctx.entities or _SUPPLIER not in ctx.ql:
        return None
    if not any(k in ctx.ql for k in _SUPPLY_JOIN_CUES):
        return None
    ent = ctx.entities[0]
    return _two_hop(
        _TwoHop(
            f"Which companies supply or supply for {ent}?",
            "list suppliers",
            ctx.q,
            "intersect suppliers",
            placeholder=False,
        )
    )


def match_shared_connections(ctx: PlanContext) -> list[SubQuestion] | None:
    if len(ctx.entities) < 2 or _SHARED not in ctx.ql:
        return None
    a, b = ctx.entities[0], ctx.entities[1]
    return [
        SubQuestion(id="sq1", text=f"What are neighbors of {a}?", rationale="expand A"),
        SubQuestion(id="sq2", text=f"What are neighbors of {b}?", rationale="expand B"),
        SubQuestion(
            id="sq3",
            text=f"What shared connections exist between {a} and {b}?",
            depends_on=["sq1", "sq2"],
            rationale="join neighbor sets",
        ),
    ]


def match_path_between(ctx: PlanContext) -> list[SubQuestion] | None:
    if len(ctx.entities) < 2:
        return None
    if not any(k in ctx.ql for k in _PATH_CUES):
        return None
    a, b = ctx.entities[0], ctx.entities[1]
    return [
        SubQuestion(
            id="sq1",
            text=f"What graph path connects {a} and {b}?",
            rationale="path query",
        ),
    ]


def match_both_entities(ctx: PlanContext) -> list[SubQuestion] | None:
    if _BOTH not in ctx.ql or len(ctx.entities) < 2:
        return None
    return [
        SubQuestion(
            id="sq1",
            text=f"What relations involve {ctx.entities[0]}?",
            rationale="facts about first entity",
        ),
        SubQuestion(
            id="sq2",
            text=f"What relations involve {ctx.entities[1]}?",
            rationale="facts about second entity",
        ),
        SubQuestion(
            id="sq3",
            text=ctx.q,
            depends_on=["sq1", "sq2"],
            rationale="combine both entity facts",
        ),
    ]


def match_single_hop_primary(ctx: PlanContext) -> list[SubQuestion] | None:
    if not ctx.primary:
        return None
    return [
        SubQuestion(
            id="sq1",
            text=ctx.q,
            rationale=f"single-hop around {ctx.primary}",
        )
    ]


# Order matters: more specific multi-hop patterns before generic ones.
OFFLINE_MATCHERS: tuple[Matcher, ...] = (
    match_ceo_of_parent,
    match_ceo_previous_work,
    match_ceo_of_competitor_of_producer,
    match_ceo_of_competitor,
    match_parent_of_producer,
    match_suppliers_intersect,
    match_shared_connections,
    match_path_between,
    match_both_entities,
    match_single_hop_primary,
)
