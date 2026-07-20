"""Complexity triage: Fast Path vs Agentic (FR-AG-01 / P3-PERF-01).

Two-level strategy:
1. Rule front-end for clear single-entity fact questions → Fast Path
2. Optional light LLM for borderline cases

Fast Path may escalate once to Agentic when evidence is clearly insufficient.
"""

from __future__ import annotations

import re
from collections.abc import Callable
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
    # Nested ownership / market chains (P3-EV-02 offline ablation finding)
    re.compile(r"\bcompetitor\b.*\b(producer|produce|product)\b", re.I),
    re.compile(r"\b(producer|product)\b.*\bcompetitor\b", re.I),
    re.compile(r"\bceo\b.+\bof\b.+\bof\b", re.I),
    re.compile(r"\bof\b.+\bof\b.+\bof\b", re.I),
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

_SHORT_Q_MAX_LEN = 80
_MAX_ENTITY_HINTS = 5
_DEFAULT_AGENTIC_HOPS = 2
_MULTI_HOP_EST = 3
_ESCALATE_MIN_EVIDENCE = 2
_HOP_CLAMP_MIN = 1
_HOP_CLAMP_MAX = 10
_DEFAULT_LLM_CONFIDENCE = 0.7
_SHORT_WH_CONFIDENCE = 0.75
_SIMPLE_FACT_CONFIDENCE = 0.9
_DEFAULT_CONFIDENCE = 0.5
_WEAK_ANSWER_STATUSES = frozenset({"no_answer", "partial"})


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


RuleFn = Callable[[str, list[str]], TriageResult | None]


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
        return _agentic("empty question", hops=1, rule="empty")
    if force_agentic:
        return _agentic("force_agentic", hops=_MULTI_HOP_EST, rule="force_agentic")

    entities = extract_entity_mentions(q, known_entities, max_entities=_MAX_ENTITY_HINTS)
    for rule in (_rule_multi_hop, _rule_simple_fact, _rule_short_wh):
        hit = rule(q, entities)
        if hit is not None:
            return hit

    if allow_llm and llm is not None:
        llm_hit = _llm_triage(q, llm)
        if llm_hit is not None:
            return llm_hit

    return _agentic(
        "default agentic (ambiguous)",
        hops=_DEFAULT_AGENTIC_HOPS,
        confidence=_DEFAULT_CONFIDENCE,
        rule="default",
    )


def _agentic(
    rationale: str,
    *,
    hops: int,
    rule: str,
    confidence: float = 1.0,
) -> TriageResult:
    return TriageResult(
        route=Route.AGENTIC,
        rationale=rationale,
        estimated_hops=hops,
        confidence=confidence,
        rule_hit=rule,
    )


def _fast(
    rationale: str,
    *,
    confidence: float,
    rule: str,
) -> TriageResult:
    return TriageResult(
        route=Route.FAST_PATH,
        rationale=rationale,
        estimated_hops=1,
        confidence=confidence,
        rule_hit=rule,
    )


def _rule_multi_hop(q: str, _entities: list[str]) -> TriageResult | None:
    for pat in _MULTI_HOP_PATTERNS:
        if pat.search(q):
            return _agentic(
                f"multi-hop cue matched: {pat.pattern[:40]}",
                hops=_MULTI_HOP_EST,
                rule="multi_hop_pattern",
            )
    return None


def _rule_simple_fact(q: str, entities: list[str]) -> TriageResult | None:
    if len(entities) > 1 or _looks_nested(q):
        return None
    for pat in _SIMPLE_PATTERNS:
        if pat.match(q):
            return _fast(
                "single-entity fact question",
                confidence=_SIMPLE_FACT_CONFIDENCE,
                rule="simple_fact",
            )
    return None


def _rule_short_wh(q: str, entities: list[str]) -> TriageResult | None:
    if len(entities) > 1 or len(q) >= _SHORT_Q_MAX_LEN:
        return None
    if re.search(r"\band\b", q, re.I) or _looks_nested(q):
        return None
    if not re.search(r"\b(who|what|which|where)\b", q, re.I):
        return None
    return _fast(
        "short single-entity WH-question",
        confidence=_SHORT_WH_CONFIDENCE,
        rule="short_wh",
    )


def _looks_nested(q: str) -> bool:
    """True when the question encodes multi-hop of-chains / market roles."""
    ql = q.lower()
    if ql.count(" of ") >= 2:
        return True
    return any(k in ql for k in ("competitor", "producer of", "parent of the"))


def _llm_triage(q: str, llm: LLMProvider) -> TriageResult | None:
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
        route = (
            Route.FAST_PATH if raw.route.lower().startswith("fast") else Route.AGENTIC
        )
        hops = max(
            _HOP_CLAMP_MIN,
            min(_HOP_CLAMP_MAX, int(raw.estimated_hops or _DEFAULT_AGENTIC_HOPS)),
        )
        return TriageResult(
            route=route,
            rationale=raw.rationale or "llm triage",
            estimated_hops=hops,
            confidence=float(raw.confidence or _DEFAULT_LLM_CONFIDENCE),
            rule_hit="llm",
        )
    except Exception:
        return None


def should_escalate_fast_path(
    evidence_count: int,
    *,
    has_graph: bool,
    answer_status: str | None = None,
) -> bool:
    """One-shot escalate Fast Path → Agentic when evidence is weak (FR-AG-01)."""
    if answer_status in _WEAK_ANSWER_STATUSES and evidence_count < _ESCALATE_MIN_EVIDENCE:
        return True
    if evidence_count == 0:
        return True
    if not has_graph and evidence_count < _ESCALATE_MIN_EVIDENCE:
        return True
    return False
