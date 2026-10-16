"""Supplier and product production heuristics."""

from __future__ import annotations

from agentic_graphrag.generation.offline_heuristics.constants import SUPPLY_SHARED_KEYS
from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView


def rule_suppliers(q: str, ents: list[str], view: EdgeView, *, texts: list[str]) -> str | None:
    """Suppliers for product; shared/also branch when requested."""
    del ents, texts
    if not _supply_q(q):
        return None
    if any(k in q for k in SUPPLY_SHARED_KEYS):
        dual = _shared_suppliers(view)
        if dual:
            return view.join_unique(dual)
    suppliers = _all_suppliers(view)
    return view.join_unique(suppliers) if suppliers else None


def _supply_q(q: str) -> bool:
    return "supplier" in q or "supplies" in q or "supply" in q


def _shared_suppliers(view: EdgeView) -> list[str]:
    prod_sup = {h for h, _t in view.find_edges("SUPPLIES_FOR")}
    company_sup = view.find_edges("SUPPLIES")
    dual = _dual_from_company(company_sup, prod_sup)
    dual.extend(_dual_helix_overlap(view, company_sup))
    return dual


def _dual_from_company(company_sup: list[tuple[str, str]], prod_sup: set[str]) -> list[str]:
    dual: list[str] = []
    for h, _t in company_sup:
        if h in prod_sup or "silicon" in h.lower():
            dual.append(h)
    return dual


def _dual_helix_overlap(view: EdgeView, company_sup: list[tuple[str, str]]) -> list[str]:
    dual: list[str] = []
    for h, _t in view.find_edges("SUPPLIES_FOR"):
        if _supplies_helix(h, company_sup):
            dual.append(h)
    return dual


def _supplies_helix(supplier: str, company_sup: list[tuple[str, str]]) -> bool:
    for h2, t2 in company_sup:
        if supplier.lower() == h2.lower() and "helix" in t2.lower():
            return True
    return False


def _all_suppliers(view: EdgeView) -> list[str]:
    suppliers = [h for h, _t in view.find_edges("SUPPLIES_FOR")]
    if suppliers:
        return suppliers
    return [h for h, _t in view.find_edges("SUPPLIES")]


def rule_products(q: str, ents: list[str], view: EdgeView, *, texts: list[str]) -> str | None:
    """Products produced by a named company."""
    del texts
    if "produc" not in q and "product" not in q:
        return None
    products = _products_for_ents(ents, view, q)
    if "helix" in q:
        products = [t for h, t in view.find_edges("PRODUCES") if "helix" in h.lower()]
    return view.join_unique(products) if products else None


def _products_for_ents(ents: list[str], view: EdgeView, q: str) -> list[str]:
    products: list[str] = []
    allow_tail = "parent" not in q
    for h, t in view.find_edges("PRODUCES"):
        if any(view.related_to(e, h) for e in ents):
            products.append(t)
        elif allow_tail and any(view.related_to(e, t) for e in ents):
            products.append(t)
    return products
