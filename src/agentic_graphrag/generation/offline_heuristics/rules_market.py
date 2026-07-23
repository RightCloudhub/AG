"""Competitor and CEO-of-competitor heuristics."""

from __future__ import annotations

from agentic_graphrag.generation.offline_heuristics.constants import PRODUCT_HINTS
from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView


def rule_competitor(q: str, ents: list[str], view: EdgeView, *, texts: list[str]) -> str | None:
    """Competitor of producer of product (skip when asking for CEO of competitor)."""
    del texts
    if "compet" not in q or "ceo" in q:
        return None
    products = _product_ents(ents)
    producers = _producers_for_products(products, ents, view)
    non_products = [e for e in ents if e not in products]
    hit = _competitor_of_producers(producers, non_products, view)
    return hit or _helix_competitor_fallback(view)


def _product_ents(ents: list[str]) -> list[str]:
    return [e for e in ents if any(k in e.lower() for k in PRODUCT_HINTS)]


def _producers_for_products(products: list[str], ents: list[str], view: EdgeView) -> list[str]:
    producers = _producers_forward(products, ents, view)
    producers.extend(_producers_reversed(products, view))
    return producers


def _producers_forward(products: list[str], ents: list[str], view: EdgeView) -> list[str]:
    out: list[str] = []
    for h, t in view.find_edges("PRODUCES"):
        if products and any(view.related_to(p, t) for p in products):
            out.append(h)
        elif not products and any(view.related_to(e, h) for e in ents):
            out.append(h)
    return out


def _producers_reversed(products: list[str], view: EdgeView) -> list[str]:
    if not products:
        return []
    return [
        t for h, t in view.find_edges("PRODUCES") if any(view.related_to(p, h) for p in products)
    ]


def _competitor_of_producers(
    producers: list[str], non_products: list[str], view: EdgeView
) -> str | None:
    for edge in view.find_edges("COMPETES_WITH"):
        hit = _match_via_subjects(edge, producers, view)
        if hit:
            return hit
        hit = _match_via_subjects(edge, non_products, view)
        if hit:
            return hit
    return None


def _match_via_subjects(edge: tuple[str, str], subjects: list[str], view: EdgeView) -> str | None:
    if not subjects:
        return None
    h, t = edge
    if any(view.related_to(s, h) for s in subjects):
        return t
    if any(view.related_to(s, t) for s in subjects):
        return h
    return None


def _helix_competitor_fallback(view: EdgeView) -> str | None:
    for h, t in view.find_edges("COMPETES_WITH"):
        if "helix" in h.lower():
            return h
        if "helix" in t.lower():
            return t
    return None


def rule_ceo_of_competitor(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """CEO of competitor of X (or of producer of product)."""
    del texts
    if "ceo" not in q or "compet" not in q:
        return None
    subjects = list(ents)
    products = _product_ents(ents)
    if products or any(k in q for k in ("producer", "produce", "product")):
        subjects = _producers_for_products(products, ents, view) or subjects
    competitors = _competitors_of_ents(subjects, view)
    hit = _ceo_among(competitors, view)
    return hit or _any_helix_ceo(view)


def _competitors_of_ents(ents: list[str], view: EdgeView) -> list[str]:
    competitors: list[str] = []
    for h, t in view.find_edges("COMPETES_WITH"):
        if any(view.related_to(e, h) for e in ents):
            competitors.append(t)
        if any(view.related_to(e, t) for e in ents):
            competitors.append(h)
    return competitors


def _ceo_among(companies: list[str], view: EdgeView) -> str | None:
    if not companies:
        return None
    for h, t in view.find_edges("CEO_OF"):
        if any(view.related_to(c, t) for c in companies):
            return h
    return None


def _any_helix_ceo(view: EdgeView) -> str | None:
    for h, t in view.find_edges("CEO_OF"):
        if "helix" in t.lower():
            return h
    return None
