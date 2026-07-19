"""Complexity triage: Fast Path vs Agentic (FR-AG-01 / P3-PERF-01).

Two-level strategy:
1. Rule front-end for clear single-entity fact questions → Fast Path
2. Optional light LLM for borderline cases

Fast Path may escalate once to Agentic when evidence is clearly insufficient.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured

# Multi-hop / complex cues → force Agentic
_MULTI_HOP_PATTERNS = (
    re.compile(r"\bparent\b.*\bceo\b|\bceo\b.*\bparent\b", re.I),
    re.compile(r"\b(both|and also|as well as)\b", re.I),
    re.compile(r"\b(who|what).+\b(who|what)\b", re.I),
    re.compile(r"\b(previously|before|then|after|now)\b", re.I),
    re.compile(r"\b(among|between|compare|relationship|chain|path)\b", re.I),
    re.compile(r"\b(supplier|subsidiary).+\b(of|to)\b.+\b(of|to)\b", re.I),
    re.compile(r"\{from:sq", re.I),
)

# Simple fact patterns → Fast Path when only one entity
_SIMPLE_PATTERNS = (
    re.compile(r"^(who|what)\s+is\s+the\s+(ceo|cfo|cto|president)\s+of\s+.+\??$", re.I),
    re.compile(r"^what\s+is\s+the\s+parent\s+(company\s+)?of\s+.+\??$", re.I),
    re.compile(r"^who\s+(founded|owns|leads)\s+.+\??$", re.I),
    re.compile(r"^what\s+(type|category)\s+is\s+.+\??$", re.I),
    re.compile(r"^list\s+(the\s+)?(products|suppliers)\s+of\s+.+\??$", re.I),
)


class Route(StrEnum):
    FAST_PATH = "fast_path"
    AGENTIC = "agentic"


class TriageResult(BaseModel):
    route: Route
    rationale: str = ""
    estimated_hops: int = Field(default=1, ge=1, le=10)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rule_hit: str = ""


class _LlmTriage(BaseModel):
    route: str = "agentic"
    estimated_hops: int = 2
    rationale: str = ""
    confidence: float = 0.7


def triage(
    question: str,
    llm: LLMProvider | None = None,
    *,
    allow_llm: bool = True,
    force_agentic: bool = False,
    known_entities: list[str] | None = None,
) -> TriageResult:
    """Classify question complexity and choose Fast Path or Agentic."""
    q = (question or "").strip()
    if not q:
        return TriageResult(
            route=Route.AGENTIC,
            rationale="empty question",
            estimated_hops=1,
            rule_hit="empty",
        )
    if force_agentic:
        return TriageResult(
            route=Route.AGENTIC,
            rationale="force_agentic",
            estimated_hops=3,
            rule_hit="force_agentic",
        )

    for pat in _MULTI_HOP_PATTERNS:
        if pat.search(q):
            return TriageResult(
                route=Route.AGENTIC,
                rationale=f"multi-hop cue matched: {pat.pattern[:40]}",
                estimated_hops=3,
                rule_hit="multi_hop_pattern",
            )

    entities = extract_entity_mentions(q, known_entities, max_entities=5)
    for pat in _SIMPLE_PATTERNS:
        if pat.match(q) and len(entities) <= 1:
            return TriageResult(
                route=Route.FAST_PATH,
                rationale="single-entity fact question",
                estimated_hops=1,
                confidence=0.9,
                rule_hit="simple_fact",
            )

    # Short questions with one entity and no multi-hop cues → Fast Path
    if len(entities) <= 1 and len(q) < 80 and not re.search(r"\band\b", q, re.I):
        if re.search(r"\b(who|what|which|where)\b", q, re.I):
            return TriageResult(
                route=Route.FAST_PATH,
                rationale="short single-entity WH-question",
                estimated_hops=1,
                confidence=0.75,
                rule_hit="short_wh",
            )

    # Borderline: optional light LLM
    if allow_llm and llm is not None:
        try:
            raw = complete_structured(
                llm,
                [
                    Message(
                        role="system",
                        content=(
                            "Classify QA complexity. route=fast_path for single-hop "
                            "fact lookups; route=agentic for multi-hop, comparison, "
                            "or multi-entity reasoning. Reply with JSON only."
                        ),
                    ),
                    Message(role="user", content=f"Question: {q}"),
                ],
                _LlmTriage,
                tier=Tier.LIGHT,
            )
            route = Route.FAST_PATH if raw.route.lower().startswith("fast") else Route.AGENTIC
            return TriageResult(
                route=route,
                rationale=raw.rationale or "llm triage",
                estimated_hops=max(1, min(10, int(raw.estimated_hops or 2))),
                confidence=float(raw.confidence or 0.7),
                rule_hit="llm",
            )
        except Exception:
            pass

    return TriageResult(
        route=Route.AGENTIC,
        rationale="default agentic (ambiguous)",
        estimated_hops=2,
        confidence=0.5,
        rule_hit="default",
    )


def should_escalate_fast_path(
    evidence_count: int,
    *,
    has_graph: bool,
    answer_status: str | None = None,
) -> bool:
    """One-shot escalate Fast Path → Agentic when evidence is weak (FR-AG-01)."""
    if answer_status in {"no_answer", "partial"} and evidence_count < 2:
        return True
    if evidence_count == 0:
        return True
    if not has_graph and evidence_count < 2:
        return True
    return False
