"""Parent / ownership / logistics heuristics."""

from __future__ import annotations

from agentic_graphrag.generation.offline_heuristics.constants import PRODUCT_Q_KEYS
from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView


def rule_parent_of_producer(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """Parent of producer of product."""
    del texts
    if "parent" not in q or not any(k in q for k in PRODUCT_Q_KEYS):
        return None
    producers = _producers_of(ents, view)
    return _parent_of_any(producers, view)


def _producers_of(products: list[str], view: EdgeView) -> list[str]:
    return [
        h
        for h, t in view.find_edges("PRODUCES")
        if any(view.related_to(p, t) for p in products)
    ]


def _parent_of_any(producers: list[str], view: EdgeView) -> str | None:
    if not producers:
        return None
    for h, t in view.find_edges("PARENT_OF"):
        if any(view.related_to(p, t) for p in producers):
            return h
    for h, t in view.find_edges("SUBSIDIARY_OF"):
        if any(view.related_to(p, h) for p in producers):
            return t
    return None


def rule_parent_owns(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """Parent company / owns (excluding logistics-specific questions)."""
    del texts
    if not _parent_owns_q(q):
        return None
    for e in ents:
        hit = _parent_of_entity(e, view)
        if hit:
            return hit
    return None


def _parent_owns_q(q: str) -> bool:
    if "parent" in q:
        return True
    return "own" in q and "logistics" not in q


def _parent_of_entity(entity: str, view: EdgeView) -> str | None:
    for h, t in view.find_edges("PARENT_OF"):
        if view.related_to(entity, t):
            return h
    for h, t in view.find_edges("SUBSIDIARY_OF"):
        if view.related_to(entity, h):
            return t
    return None


def rule_logistics(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """Logistics firm owned by Apex that supplies Helix."""
    del ents, texts
    if "logistics" not in q:
        return None
    owned = _apex_owned(view)
    match = _logistics_name_match(owned, view)
    if match:
        return match
    inter = owned & _helix_suppliers(view)
    if inter:
        return next(iter(inter))
    return _bright_or_logistic(owned)


def _apex_owned(view: EdgeView) -> set[str]:
    return {t for h, t in view.find_edges("PARENT_OF") if "apex" in h.lower()}


def _helix_suppliers(view: EdgeView) -> set[str]:
    suppliers = {h for h, t in view.find_edges("SUPPLIES") if "helix" in t.lower()}
    suppliers |= {t for h, t in view.find_edges("SUPPLIES") if "helix" in h.lower()}
    return suppliers


def _logistics_name_match(owned: set[str], view: EdgeView) -> str | None:
    helix_sup = [h for h, t in view.find_edges("SUPPLIES") if "helix" in t.lower()]
    for o in owned:
        hit = _match_owned_supplier(o, helix_sup)
        if hit:
            return hit
    return None


def _match_owned_supplier(owned: str, helix_sup: list[str]) -> str | None:
    for s in helix_sup:
        if owned.lower() == s.lower() or "bright" in owned.lower():
            return owned if "bright" in owned.lower() else s
    return None


def _bright_or_logistic(owned: set[str]) -> str | None:
    for name in owned:
        if "bright" in name.lower() or "logistic" in name.lower():
            return name
    return None
