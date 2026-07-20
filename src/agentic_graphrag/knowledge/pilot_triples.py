"""Deterministic triples for the synthetic pilot corpus (P2-KG-04 / P2-EV-02).

Mirrors the shared universe in ``scripts/generate_pilot_corpus.py`` so graph
build and gold-case generation do not require live LLM extract for the
engineering pilot. When a product-authorized corpus arrives, replace with
``run_extract_pipeline`` + human spotcheck.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_graphrag.config import ROOT_DIR
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple

_DEFAULT_CONF = 0.95
_CEO_TITLES = frozenset({"CEO", "MANAGING DIRECTOR"})


@dataclass(frozen=True)
class TripleSpec:
    head: str
    htype: str
    rel: str
    tail: str
    ttype: str
    conf: float = _DEFAULT_CONF
    span: str = ""
    doc_id: str = "pilot"
    chunk_id: str = "pilot:0"


def _load_pilot_module() -> Any:
    path = ROOT_DIR / "scripts" / "generate_pilot_corpus.py"
    spec = importlib.util.spec_from_file_location("agr_generate_pilot_corpus", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pilot corpus generator: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _t(spec: TripleSpec) -> Triple:
    return Triple(
        head=EntityMention(name=spec.head, type=spec.htype),
        relation=spec.rel,
        tail=EntityMention(name=spec.tail, type=spec.ttype),
        confidence=spec.conf,
        source_span=spec.span or f"{spec.head} {spec.rel} {spec.tail}",
        source_doc_id=spec.doc_id,
        source_chunk_id=spec.chunk_id,
    )


class _TripleBag:
    def __init__(self) -> None:
        self.triples: list[Triple] = []
        self.seen: set[tuple[str, str, str]] = set()

    def add(self, t: Triple) -> None:
        key = (t.head.name.lower(), t.relation.upper(), t.tail.name.lower())
        if key in self.seen:
            return
        self.seen.add(key)
        self.triples.append(t)

    def add_spec(self, spec: TripleSpec) -> None:
        self.add(_t(spec))


def build_pilot_triples() -> list[Triple]:
    """Emit schema-valid triples covering ownership, employment, products, supply, events."""
    mod = _load_pilot_module()
    bag = _TripleBag()
    _add_core_holdings(bag)
    _add_expanded(bag, mod)
    _add_people(bag, mod)
    _add_products(bag, mod)
    _add_events(bag, mod)
    _add_seed_file(bag)
    return bag.triples


def _add_core_holdings(bag: _TripleBag) -> None:
    core = [
        ("Apex Holdings", "PARENT_OF", "NovaTech Industries"),
        ("Apex Holdings", "PARENT_OF", "BrightLink Logistics"),
        ("NovaTech Industries", "SUBSIDIARY_OF", "Apex Holdings"),
        ("BrightLink Logistics", "SUBSIDIARY_OF", "Apex Holdings"),
        ("NovaTech Industries", "COMPETES_WITH", "Helix Compute"),
        ("Helix Compute", "COMPETES_WITH", "NovaTech Industries"),
        ("SiliconForge", "SUPPLIES", "NovaTech Industries"),
        ("SiliconForge", "SUPPLIES", "Helix Compute"),
        ("Harbor Components", "SUPPLIES", "NovaTech Industries"),
        ("BrightLink Logistics", "SUPPLIES", "Helix Compute"),
    ]
    for head, rel, tail in core:
        bag.add_spec(TripleSpec(head, "Company", rel, tail, "Company"))


def _add_expanded(bag: _TripleBag, mod: Any) -> None:
    doc = "pilot_expanded"
    for name, _city, _ind, _year, parent, competitors, products in mod.EXPANDED:
        if parent:
            bag.add_spec(
                TripleSpec(name, "Company", "SUBSIDIARY_OF", parent, "Company", doc_id=doc)
            )
            bag.add_spec(
                TripleSpec(parent, "Company", "PARENT_OF", name, "Company", doc_id=doc)
            )
        for comp in competitors:
            bag.add_spec(TripleSpec(name, "Company", "COMPETES_WITH", comp, "Company", doc_id=doc))
            bag.add_spec(TripleSpec(comp, "Company", "COMPETES_WITH", name, "Company", doc_id=doc))
        for prod in products:
            bag.add_spec(TripleSpec(name, "Company", "PRODUCES", prod, "Product", doc_id=doc))


def _add_people(bag: _TripleBag, mod: Any) -> None:
    doc = "pilot_people"
    for person, title, company, prior, _loc in mod.PEOPLE:
        rel = "CEO_OF" if title.upper() in _CEO_TITLES else "WORKS_AT"
        bag.add_spec(
            TripleSpec(
                person,
                "Person",
                rel,
                company,
                "Company",
                doc_id=doc,
                span=f"{person} is {title} of {company}",
            )
        )
        for prev in prior:
            bag.add_spec(
                TripleSpec(
                    person,
                    "Person",
                    "WORKED_AT",
                    prev,
                    "Company",
                    doc_id=doc,
                    span=f"{person} previously worked at {prev}",
                )
            )


def _add_products(bag: _TripleBag, mod: Any) -> None:
    doc = "pilot_products"
    for product, producer, suppliers, _cat in mod.PRODUCTS_EXTRA:
        bag.add_spec(
            TripleSpec(producer, "Company", "PRODUCES", product, "Product", doc_id=doc)
        )
        for sup in suppliers:
            bag.add_spec(
                TripleSpec(sup, "Company", "SUPPLIES_FOR", product, "Product", doc_id=doc)
            )
            bag.add_spec(
                TripleSpec(sup, "Company", "SUPPLIES", producer, "Company", doc_id=doc)
            )


def _add_events(bag: _TripleBag, mod: Any) -> None:
    doc = "pilot_events"
    for ename, date, etype, orgs, desc in mod.EVENTS:
        for org in orgs:
            bag.add_spec(
                TripleSpec(
                    org,
                    "Company",
                    "PARTICIPATED_IN",
                    ename,
                    "Event",
                    doc_id=doc,
                    span=f"{org} participated in {ename} ({date}, {etype}): {desc}",
                )
            )


def _add_seed_file(bag: _TripleBag) -> None:
    seed_path = ROOT_DIR / "data" / "processed" / "seed_triples.jsonl"
    if not seed_path.exists():
        return
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        bag.add(Triple.model_validate(json.loads(line)))


def write_pilot_triples(path: str | Path) -> list[Triple]:
    triples = build_pilot_triples()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for t in triples:
            f.write(json.dumps(t.model_dump(), ensure_ascii=False) + "\n")
    return triples
