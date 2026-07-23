"""M&A / ownership multi-hop heuristics (acquirer CEO + HQ), EN + ZH."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView
from agentic_graphrag.generation.offline_heuristics.rules_ma_parse import (
    ceos_from_edges,
    ceos_from_texts,
    hq_from_texts,
    ownership_from_edges,
    ownership_from_texts,
)

# Acquisition / ownership wording (EN + ZH). Corpus maps these to PARENT_OF.
_ACQUIRE_KEYS = (
    "acquir",
    "acquired",
    "acquisition",
    "bought",
    "owns",
    "owned",
    "parent",
    "收购",
    "被收购",
    "控股",
    "旗下",
    "母公司",
)
_HQ_KEYS = (
    "headquarter",
    "headquarters",
    "hq",
    "which city",
    "what city",
    "hq city",
    "based in",
    "located",
    "总部",
    "位于",
    "坐落",
    "哪个城市",
)
_CEO_KEYS = ("ceo", "chief executive", "首席执行官", "总裁")


@dataclass(frozen=True)
class _MaFacts:
    parent_child: dict[str, set[str]]
    ceo_map: dict[str, str]
    hq_map: dict[str, str]


def rule_acquirer_ceo_and_hq(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """CEO of acquirer/parent + HQ of target, or parent-owned companies + HQ.

    Covers both bilingual framings of the same multi-hop fact pattern:
    - EN: CEO of company that acquired NovaTech + NovaTech HQ city
    - ZH: Apex 的 CEO 所在公司收购了谁 + 被收购方总部城市
    """
    if not _is_ma_multi_hop_q(q):
        return None
    # Graph ownership is authoritative; free-text fills only when edges are missing
    # (avoids pilot-corpus subsidiaries polluting seed-graph answers).
    edge_own = ownership_from_edges(view)
    parent_child = edge_own or ownership_from_texts(texts)
    facts = _MaFacts(
        parent_child=parent_child,
        ceo_map={**ceos_from_edges(view), **ceos_from_texts(texts)},
        hq_map=hq_from_texts(texts),
    )
    return _answer_from_target(ents, facts) or _answer_from_parent(ents, facts)


def _is_ma_multi_hop_q(q: str) -> bool:
    has_acq = any(k in q for k in _ACQUIRE_KEYS)
    has_hq = any(k in q for k in _HQ_KEYS)
    has_ceo = any(k in q for k in _CEO_KEYS)
    return (has_acq and has_hq) or (has_acq and has_ceo)


def _answer_from_target(ents: list[str], facts: _MaFacts) -> str | None:
    if not ents:
        return None
    for ent in ents:
        parent, child = _parent_of(ent, facts.parent_child)
        if not parent:
            continue
        ceo = _lookup(facts.ceo_map, parent)
        hq = _lookup(facts.hq_map, child) or _lookup(facts.hq_map, ent)
        return _format_target_answer(ceo=ceo, parent=parent, target=child, hq=hq)
    return None


def _answer_from_parent(ents: list[str], facts: _MaFacts) -> str | None:
    if not ents:
        return None
    for ent in ents:
        kids = _children_of(ent, facts.parent_child)
        if not kids:
            continue
        parent = _canonical_parent_name(ent, facts.parent_child)
        ceo = _lookup(facts.ceo_map, parent) or _lookup(facts.ceo_map, ent)
        ordered = _order_children(kids, facts.hq_map)
        primary = ordered[0]
        return _format_parent_answer(
            ceo=ceo,
            parent=parent,
            primary=primary,
            hq=_lookup(facts.hq_map, primary),
            others=ordered[1:],
        )
    return None


def _parent_of(ent: str, parent_child: dict[str, set[str]]) -> tuple[str | None, str]:
    el = ent.lower()
    for parent_l, kids in parent_child.items():
        for kid in kids:
            if el in kid.lower() or kid.lower() in el:
                return _title_parent(parent_l), kid
    return None, ent


def _children_of(ent: str, parent_child: dict[str, set[str]]) -> list[str]:
    el = ent.lower()
    for parent_l, kids in parent_child.items():
        if el in parent_l or parent_l in el:
            return list(kids)
    return []


def _canonical_parent_name(ent: str, parent_child: dict[str, set[str]]) -> str:
    el = ent.lower()
    for parent_l in parent_child:
        if el in parent_l or parent_l in el:
            return _title_parent(parent_l)
    return ent


def _title_parent(parent_l: str) -> str:
    return " ".join(w.capitalize() for w in parent_l.split())


def _order_children(kids: list[str], hq_map: dict[str, str]) -> list[str]:
    """Prefer subsidiaries with HQ facts; de-prioritize pure logistics names."""

    def rank(name: str) -> tuple[int, int, str]:
        has_hq = 0 if _lookup(hq_map, name) else 1
        is_logistics = 1 if "logistic" in name.lower() else 0
        return (has_hq, is_logistics, name.lower())

    return sorted(kids, key=rank)


def _lookup(mapping: dict[str, str], name: str | None) -> str | None:
    if not name:
        return None
    nl = name.lower()
    if nl in mapping:
        return mapping[nl]
    for key, val in mapping.items():
        if key in nl or nl in key:
            return val
    return None


def _format_target_answer(
    *,
    ceo: str | None,
    parent: str,
    target: str | None,
    hq: str | None,
) -> str | None:
    if not target:
        return None
    if ceo:
        parts = [f"{ceo} is the CEO of {parent}, the company that acquired/owns {target}."]
    else:
        parts = [f"{parent} acquired/owns {target}."]
    if hq:
        parts.append(f"{target} is headquartered in {hq}.")
    return " ".join(parts)


def _format_parent_answer(
    *,
    ceo: str | None,
    parent: str,
    primary: str,
    hq: str | None,
    others: list[str],
) -> str | None:
    if ceo:
        bits = [f"{ceo} is the CEO of {parent}. {parent} acquired/owns {primary}."]
    else:
        bits = [f"{parent} acquired/owns {primary}."]
    if hq:
        bits.append(f"{primary} is headquartered in {hq}.")
    if others:
        bits.append(f"Also owns: {', '.join(others)}.")
    return " ".join(bits)
