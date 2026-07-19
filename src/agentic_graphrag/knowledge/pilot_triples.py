"""Deterministic triples for the synthetic pilot corpus (P2-KG-04 / P2-EV-02).

Mirrors the shared universe in ``scripts/generate_pilot_corpus.py`` so graph
build and gold-case generation do not require live LLM extract for the
engineering pilot. When a product-authorized corpus arrives, replace with
``run_extract_pipeline`` + human spotcheck.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from agentic_graphrag.config import ROOT_DIR
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple


def _load_pilot_module() -> Any:
    path = ROOT_DIR / "scripts" / "generate_pilot_corpus.py"
    spec = importlib.util.spec_from_file_location("agr_generate_pilot_corpus", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pilot corpus generator: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _t(
    head: str,
    htype: str,
    rel: str,
    tail: str,
    ttype: str,
    *,
    conf: float = 0.95,
    span: str = "",
    doc_id: str = "pilot",
    chunk_id: str = "pilot:0",
) -> Triple:
    return Triple(
        head=EntityMention(name=head, type=htype),
        relation=rel,
        tail=EntityMention(name=tail, type=ttype),
        confidence=conf,
        source_span=span or f"{head} {rel} {tail}",
        source_doc_id=doc_id,
        source_chunk_id=chunk_id,
    )


def build_pilot_triples() -> list[Triple]:
    """Emit schema-valid triples covering ownership, employment, products, supply, events."""
    mod = _load_pilot_module()
    triples: list[Triple] = []
    seen: set[tuple[str, str, str]] = set()

    def add(t: Triple) -> None:
        key = (t.head.name.lower(), t.relation.upper(), t.tail.name.lower())
        if key in seen:
            return
        seen.add(key)
        triples.append(t)

    # Core holdings (same narrative as interim seed / core docs)
    add(_t("Apex Holdings", "Company", "PARENT_OF", "NovaTech Industries", "Company"))
    add(_t("Apex Holdings", "Company", "PARENT_OF", "BrightLink Logistics", "Company"))
    add(_t("NovaTech Industries", "Company", "SUBSIDIARY_OF", "Apex Holdings", "Company"))
    add(_t("BrightLink Logistics", "Company", "SUBSIDIARY_OF", "Apex Holdings", "Company"))
    add(_t("NovaTech Industries", "Company", "COMPETES_WITH", "Helix Compute", "Company"))
    add(_t("Helix Compute", "Company", "COMPETES_WITH", "NovaTech Industries", "Company"))
    add(_t("SiliconForge", "Company", "SUPPLIES", "NovaTech Industries", "Company"))
    add(_t("SiliconForge", "Company", "SUPPLIES", "Helix Compute", "Company"))
    add(_t("Harbor Components", "Company", "SUPPLIES", "NovaTech Industries", "Company"))
    add(_t("BrightLink Logistics", "Company", "SUPPLIES", "Helix Compute", "Company"))

    # Expanded subsidiaries / parents / competitors
    for name, _city, _ind, _year, parent, competitors, products in mod.EXPANDED:
        if parent:
            add(_t(name, "Company", "SUBSIDIARY_OF", parent, "Company", doc_id="pilot_expanded"))
            add(_t(parent, "Company", "PARENT_OF", name, "Company", doc_id="pilot_expanded"))
        for comp in competitors:
            add(
                _t(
                    name,
                    "Company",
                    "COMPETES_WITH",
                    comp,
                    "Company",
                    doc_id="pilot_expanded",
                )
            )
            add(
                _t(
                    comp,
                    "Company",
                    "COMPETES_WITH",
                    name,
                    "Company",
                    doc_id="pilot_expanded",
                )
            )
        for prod in products:
            add(
                _t(
                    name,
                    "Company",
                    "PRODUCES",
                    prod,
                    "Product",
                    doc_id="pilot_expanded",
                )
            )

    # People: CEO_OF / WORKS_AT / WORKED_AT
    for person, title, company, prior, _loc in mod.PEOPLE:
        title_u = title.upper()
        if title_u in {"CEO", "MANAGING DIRECTOR"}:
            # Treat MD as top executive for multi-hop CEO templates (synthetic pilot).
            add(
                _t(
                    person,
                    "Person",
                    "CEO_OF",
                    company,
                    "Company",
                    doc_id="pilot_people",
                    span=f"{person} is {title} of {company}",
                )
            )
        else:
            add(
                _t(
                    person,
                    "Person",
                    "WORKS_AT",
                    company,
                    "Company",
                    doc_id="pilot_people",
                    span=f"{person} is {title} of {company}",
                )
            )
        for prev in prior:
            add(
                _t(
                    person,
                    "Person",
                    "WORKED_AT",
                    prev,
                    "Company",
                    doc_id="pilot_people",
                    span=f"{person} previously worked at {prev}",
                )
            )

    # Products + suppliers
    for product, producer, suppliers, _cat in mod.PRODUCTS_EXTRA:
        add(
            _t(
                producer,
                "Company",
                "PRODUCES",
                product,
                "Product",
                doc_id="pilot_products",
            )
        )
        for sup in suppliers:
            add(
                _t(
                    sup,
                    "Company",
                    "SUPPLIES_FOR",
                    product,
                    "Product",
                    doc_id="pilot_products",
                )
            )
            add(
                _t(
                    sup,
                    "Company",
                    "SUPPLIES",
                    producer,
                    "Company",
                    doc_id="pilot_products",
                )
            )

    # Events
    for ename, date, etype, orgs, desc in mod.EVENTS:
        for org in orgs:
            add(
                _t(
                    org,
                    "Company",
                    "PARTICIPATED_IN",
                    ename,
                    "Event",
                    doc_id="pilot_events",
                    span=f"{org} participated in {ename} ({date}, {etype}): {desc}",
                )
            )

    # Seed-file merge (interim narrative edges if not already present)
    seed_path = ROOT_DIR / "data" / "processed" / "seed_triples.jsonl"
    if seed_path.exists():
        import json

        for line in seed_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            add(Triple.model_validate(json.loads(line)))

    return triples


def write_pilot_triples(path: str | Path) -> list[Triple]:
    import json

    triples = build_pilot_triples()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for t in triples:
            f.write(json.dumps(t.model_dump(), ensure_ascii=False) + "\n")
    return triples
