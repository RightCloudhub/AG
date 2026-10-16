"""Claim ↔ graph-edge relation consistency (docs/design/BUSINESS_LOGIC.md BL-13).

Lexical overlap plus object anchoring still lets a claim cite a real edge that
asserts a *different* relation: "Apex Holdings' CEO is Elena Varga" citing
``Elena Varga -[WORKED_AT]-> Apex Holdings`` names both endpoints. This module
reads the relation a claim asserts (bilingual cue words) and the relation(s) a
graph edge or path asserts (``-[REL]->`` in its content) and requires them to be
compatible. It works on evidence *content*, so the offline evaluator can apply
the identical check to persisted catalogs.

Claims with no relation cue, and evidence without an edge marker (text chunks),
are not judged here — that is the entailment judge's job.
"""

from __future__ import annotations

import re

_EDGE = re.compile(r"\[([A-Z][A-Z_]+)(?:#[^\]]*)?\]")

RELATION_CUES: dict[str, tuple[str, ...]] = {
    "CEO_OF": ("ceo", "chief executive", "首席执行官", "总裁", "执行长"),
    "FOUNDED": ("found", "co-found", "创立", "创办", "创始"),
    "PARENT_OF": ("parent", "owns", "owned by", "母公司", "控股", "旗下"),
    "SUBSIDIARY_OF": ("subsidiary", "子公司", "附属"),
    "ACQUIRED": ("acquir", "bought", "收购", "并购"),
    "PRODUCES": ("produce", "product", "manufactur", "makes", "生产", "产品", "制造"),
    "SUPPLIES_FOR": ("supplier", "supplies", "component", "供应", "供货", "零部件"),
    "SUPPLIES": ("supplier", "supplies", "logistics", "shipping", "供应", "物流"),
    "COMPETES_WITH": ("compet", "rival", "竞争", "竞品", "对手"),
    "WORKED_AT": ("worked", "previously", "former", "employ", "曾在", "任职", "就职"),
    "WORKS_AT": ("works at", "employ", "任职", "就职"),
    "LOCATED_IN": ("headquarter", "based in", "located", "总部", "位于"),
    "PARTICIPATED_IN": ("particip", "took part", "involved in", "参与", "参加"),
    "PARTNER_OF": ("partner", "合作", "伙伴"),
}

# Relations that state the same fact from another side or at another grain.
_COMPATIBLE: tuple[frozenset[str], ...] = (
    frozenset({"PARENT_OF", "SUBSIDIARY_OF", "ACQUIRED"}),
    frozenset({"WORKED_AT", "WORKS_AT"}),
    frozenset({"SUPPLIES", "SUPPLIES_FOR"}),
    frozenset({"PARTICIPATED_IN", "ACQUIRED"}),
)


# One-way entailment: an edge of the value type proves a claim of the key type
# (a CEO works at the company), never the other way round.
_IMPLIED_BY: dict[str, frozenset[str]] = {
    "WORKS_AT": frozenset({"CEO_OF"}),
    "WORKED_AT": frozenset({"CEO_OF", "FOUNDED"}),
}


def claim_relations(text: str) -> set[str]:
    """Relation types a claim asserts, by cue words (empty = no detectable relation)."""
    lowered = (text or "").lower()
    return {rel for rel, cues in RELATION_CUES.items() if any(c in lowered for c in cues)}


def edge_relations(content: str) -> set[str]:
    """Relation types a graph edge/path content asserts (empty for text chunks)."""
    return set(_EDGE.findall(content or ""))


def expand_compatible(relations: set[str]) -> set[str]:
    out = set(relations)
    for group in _COMPATIBLE:
        if out & group:
            out |= group
    for rel in list(out):
        out |= _IMPLIED_BY.get(rel, frozenset())
    return out


def relation_consistent(claim_text: str, evidence_content: str) -> bool:
    """False only when both sides assert relations and none of them are compatible."""
    asserted = claim_relations(claim_text)
    stated = edge_relations(evidence_content)
    if not asserted or not stated:
        return True
    return bool(expand_compatible(asserted) & stated)
