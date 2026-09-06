#!/usr/bin/env python3
"""Generate the "Corporate Timelines" corpus: docs + temporal triples.

Corporate-relations domain (configs/schema/domain_v0.yaml) with EXPLICIT
temporal validity (ADR-007 / BL-14): CEO successions, prior employments,
product line launches, and acquisitions all carry valid_from / valid_to.

The corpus is triples-first: docs are rendered from the same data structures
that emit the triples, so the two can never drift (the interim pilot corpus
mirrors a script instead — see knowledge/pilot_triples.py).

Outputs:
  data/temporal/raw/*.md                      (docs, gitignored)
  data/processed/temporal_triples.jsonl       (triples with temporal windows)
  reports/temporal_corpus/bl14_drill.json     (BL-14 conflict-behavior drill)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "data" / "temporal" / "raw"
TRIPLES_PATH = ROOT / "data" / "processed" / "temporal_triples.jsonl"
REPORT_DIR = ROOT / "reports" / "temporal_corpus"

# ── Shared universe ─────────────────────────────────────────────────────────

PARENTS = [
    ("Meridian Group", "London", "conglomerate", 1985, "David Kim"),
    ("Harbor Ventures", "Singapore", "investment group", 1990, "Sofia Alvarez"),
    ("Northgate Holdings", "Chicago", "industrial holdings", 1978, "Grace Lee"),
]

# (name, hq, industry, founded, parent_index)
SUBSIDIARIES = [
    ("Titan Materials", "Leeds", "composites", 2001, 0),
    ("Polar Computing", "Helsinki", "hardware", 2006, 0),
    ("Ivory Lane", "Dublin", "payments", 2012, 0),
    ("Solstice Foods", "Lyon", "food brands", 2008, 0),
    ("Harbor Grid", "Rotterdam", "energy distribution", 2004, 1),
    ("Keystone Optics", "Zurich", "photonics", 2009, 1),
    ("Lantern Media", "Melbourne", "streaming", 2014, 1),
    ("Copperline Mining", "Perth", "mining", 1998, 1),
    ("Northgate Robotics", "Detroit", "automation", 2005, 2),
    ("Fieldstone AgTech", "Des Moines", "agriculture software", 2013, 2),
    ("Vault & Ledger", "Frankfurt", "fintech infrastructure", 2010, 2),
    ("Summit Rail", "Oslo", "rail logistics", 2002, 2),
]

# Companies whose CEO role changed over time (BL-14 succession pairs).
# These are leaf companies: no products, no workers, no children — gold
# templates read the first CEO edge of a parent, so succession ambiguity is
# confined to questions that name the person or the window explicitly.
# (name, hq, industry, founded, parent_idx, first_ceo, second_ceo, w1, w2)
SUCCESSIONS = [
    (
        "Quillon Energy",
        "Stavanger",
        "offshore services",
        1999,
        1,
        "Nora Berg",
        "Tomas Ruiz",
        "2015",
        "2021",
    ),
    (
        "Fable Studios",
        "Copenhagen",
        "animation",
        2011,
        0,
        "Iris Nova",
        "Kenji Watanabe",
        "2016",
        "2022",
    ),
    (
        "Drift & Anchor",
        "Lisbon",
        "maritime software",
        2007,
        2,
        "Paulo Alves",
        "Marta Kim",
        "2014",
        "2020",
    ),
    (
        "Amber Casino Group",
        "Valletta",
        "hospitality",
        2003,
        0,
        "Lena Fischer",
        "Omar Haddad",
        "2017",
        "2023",
    ),
    (
        "Pinecrest Timber",
        "Vancouver",
        "forestry",
        1995,
        2,
        "Erik Strand",
        "Ava Whitecloud",
        "2013",
        "2019",
    ),
    (
        "Basalt Cement",
        "Athens",
        "construction materials",
        2000,
        1,
        "Nia Petrov",
        "Hugo Marino",
        "2015",
        "2021",
    ),
]

# (person, ceo_of_index_or_name, prior employer, prior window, hq)
BIOS = [
    ("Elena Marsh", 0, "Meridian Group", ("2012", "2019")),
    ("Jonah Pryce", 1, "Polar Computing", ("2010", "2018")),
    ("Farah Aziz", 2, "Ivory Lane", ("2014", "2021")),
    ("Marcus Bell", 3, "Solstice Foods", ("2011", "2019")),
    ("Ingrid Halvorsen", 4, "Harbor Grid", ("2013", "2020")),
    ("Chen Wei", 5, "Keystone Optics", ("2015", "2022")),
    ("Ruby O'Connell", 6, "Lantern Media", ("2016", "2023")),
    ("Sam Okoro", 7, "Copperline Mining", ("2009", "2017")),
    ("Hana Yoshida", 8, "Northgate Robotics", ("2012", "2019")),
    ("Diego Fuentes", 9, "Fieldstone AgTech", ("2014", "2021")),
    ("Clara Winter", 10, "Vault & Ledger", ("2010", "2018")),
    ("Nils Andersen", 11, "Summit Rail", ("2008", "2016")),
]

# (product, producer_index, category, launch_window or None)
PRODUCTS = [
    ("TitanWeave Panel", 0, "composite panel", ("2016", None)),
    ("PolarCore Server", 1, "edge server", None),
    ("Ivory Rail API", 2, "payments rail", ("2019", None)),
    ("Solstice Harvest", 3, "food line", None),
    ("GridNode Controller", 4, "grid controller", None),
    ("Keystone Lens Array", 5, "optics module", ("2018", None)),
    ("Lantern Stream Engine", 6, "streaming engine", None),
    ("Copperline Assay Kit", 7, "assay kit", None),
    ("Northgate Arm X2", 8, "robot arm", ("2020", None)),
    ("Fieldstone Yield OS", 9, "agri OS", None),
    ("VaultGate Gateway", 10, "banking gateway", ("2017", None)),
    ("Summit Freight OS", 11, "rail OS", None),
]

# Second product lines for four producers (distinct tails per window — the
# successor line replaces the first in time, never the same (head, tail) pair).
PRODUCT_LINES = [
    ("TitanWeave Panel Pro", 0, "composite panel", ("2021", None)),
    ("Ivory Rail API X", 2, "payments rail", ("2022", None)),
    ("Keystone Lens Array 2", 5, "optics module", ("2023", None)),
    ("Northgate Arm X3", 8, "robot arm", ("2024", None)),
]

# (acquirer_index, target, year, deal note)
ACQUISITIONS = [
    (0, "Halcyon Coatings", "2019", "coatings maker folded into Titan Materials"),
    (0, "Bastion Trust", "2021", "trust services absorbed by Ivory Lane"),
    (1, "Tidewater Instruments", "2018", "marine instruments merged into Harbor Grid"),
    (1, "Prisma Analytics", "2022", "analytics team folded into Lantern Media"),
    (2, "Ironwood Controls", "2020", "controls maker merged into Northgate Robotics"),
    (2, "Furrow Data", "2023", "field-data platform folded into Fieldstone AgTech"),
    (1, "Gullwing Logistics", "2021", "last-mile network merged into Summit Rail"),
    (0, "Cranberry Labs", "2023", "food-science lab folded into Solstice Foods"),
]

COMPETES = [
    (0, "Basalt Cement"),
    (1, "Quillon Energy"),
    (2, "Bastion Trust"),
    (4, "Gullwing Logistics"),
    (8, "Ironwood Controls"),
    (9, "Furrow Data"),
    (10, "Prisma Analytics"),
    (11, "Tidewater Instruments"),
]


def _triple(
    head: str,
    htype: str,
    rel: str,
    tail: str,
    ttype: str,
    conf: float,
    doc: str,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> Triple:
    return Triple(
        head=EntityMention(name=head, type=htype),
        relation=rel,
        tail=EntityMention(name=tail, type=ttype),
        confidence=conf,
        source_doc_id=doc,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def sub_name(idx: int | str) -> str:
    return idx if isinstance(idx, str) else SUBSIDIARIES[idx][0]


def build_triples() -> list[Triple]:
    triples: list[Triple] = []
    add = triples.append

    for name, hq, _ind, _f, ceo in PARENTS:
        d = f"parent_{name.lower().replace(' ', '_')}"
        add(_triple(name, "Company", "LOCATED_IN", hq, "Location", 0.95, d))
        add(_triple(ceo, "Person", "CEO_OF", name, "Company", 0.95, d))

    for i, (name, hq, _ind, _founded, parent_idx) in enumerate(SUBSIDIARIES):
        d = f"sub_{name.lower().replace(' ', '_')}"
        parent = PARENTS[parent_idx][0]
        add(_triple(name, "Company", "SUBSIDIARY_OF", parent, "Company", 0.95, d))
        add(_triple(parent, "Company", "PARENT_OF", name, "Company", 0.95, d))
        add(_triple(name, "Company", "LOCATED_IN", hq, "Location", 0.9, d))
        person = BIOS[i][0]
        add(_triple(person, "Person", "CEO_OF", name, "Company", 0.95, d))

    for name, _hq, _ind, _f, parent_idx, first, second, w1, w2 in SUCCESSIONS:
        d = f"succession_{name.lower().replace(' ', '_')}"
        parent = PARENTS[parent_idx][0]
        add(_triple(name, "Company", "SUBSIDIARY_OF", parent, "Company", 0.95, d))
        add(_triple(parent, "Company", "PARENT_OF", name, "Company", 0.95, d))
        add(
            _triple(first, "Person", "CEO_OF", name, "Company", 0.95, d, valid_from=w1, valid_to=w2)
        )
        add(
            _triple(
                second,
                "Person",
                "CEO_OF",
                name,
                "Company",
                0.95,
                d,
                valid_from=str(int(w2)),
                valid_to=None,
            )
        )

    for person, _ceo_idx, prior, window in BIOS:
        d = f"bio_{person.lower().replace(' ', '_').replace(chr(39), '')}"
        add(
            _triple(
                person,
                "Person",
                "WORKED_AT",
                prior,
                "Company",
                0.9,
                d,
                valid_from=window[0],
                valid_to=window[1],
            )
        )

    for product, producer_idx, _category, window in PRODUCTS:
        d = f"product_{product.lower().replace(' ', '_')}"
        producer = sub_name(producer_idx)
        # A windowed product keeps only the windowed edge — emitting both a
        # plain and a windowed PRODUCES for the same (head, tail) would put
        # duplicate edges in the graph and ambiguity into gold questions.
        add(
            _triple(
                producer,
                "Company",
                "PRODUCES",
                product,
                "Product",
                0.95,
                d,
                valid_from=window[0] if window else None,
                valid_to=window[1] if window else None,
            )
        )
        add(
            _triple(
                product, "Product", "LOCATED_IN", SUBSIDIARIES[producer_idx][1], "Location", 0.8, d
            )
        )

    for product, producer_idx, _category, window in PRODUCT_LINES:
        d = f"productline_{product.lower().replace(' ', '_')}"
        producer = sub_name(producer_idx)
        add(
            _triple(
                producer,
                "Company",
                "PRODUCES",
                product,
                "Product",
                0.95,
                d,
                valid_from=window[0],
                valid_to=window[1],
            )
        )

    for acq_idx, target, year, _note in ACQUISITIONS:
        d = f"acq_{target.lower().replace(' ', '_')}"
        acquirer = PARENTS[acq_idx][0]
        add(_triple(acquirer, "Company", "ACQUIRED", target, "Company", 0.9, d, valid_from=year))

    for a_idx, b_name in COMPETES:
        d = f"competes_{a_idx}_{b_name.lower().replace(' ', '_')}"
        add(_triple(sub_name(a_idx), "Company", "COMPETES_WITH", b_name, "Company", 0.9, d))

    return triples


# ── Document rendering ──────────────────────────────────────────────────────


def _write(path: Path, body: str, counter: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    counter[0] += 1


def render_docs(counter: list) -> None:
    if DOCS_DIR.exists():
        for p in DOCS_DIR.iterdir():
            if p.is_file():
                p.unlink()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    for name, hq, ind, founded, ceo in PARENTS:
        body = (
            f"# {name} — Group Profile\n\n"
            f"{name} is a {ind} group headquartered in {hq}, founded in {founded}.\n"
            f"The CEO of {name} is {ceo}.\n"
        )
        _write(DOCS_DIR / f"10_parent_{name.lower().replace(' ', '_')}.md", body, counter)

    for i, (name, hq, ind, founded, parent_idx) in enumerate(SUBSIDIARIES):
        parent = PARENTS[parent_idx][0]
        person = BIOS[i][0]
        body = (
            f"# {name} — Company Profile\n\n"
            f"{name} operates in the {ind} sector and is headquartered in {hq}. "
            f"It was founded in {founded}. {name} is a subsidiary of {parent}. "
            f"The CEO of {name} is {person}.\n"
        )
        _write(DOCS_DIR / f"20_sub_{name.lower().replace(' ', '_')}.md", body, counter)

    for name, hq, ind, founded, p_idx, first, second, w1, w2 in SUCCESSIONS:
        parent = PARENTS[p_idx][0]
        body = (
            f"# {name} — Leadership Timeline\n\n"
            f"{name}, a {ind} company based in {hq} founded in {founded}, is a "
            f"subsidiary of {parent}. {first} served as CEO of {name} from {w1} "
            f"to {w2}. From {w2} onwards, {second} has served as CEO of {name}.\n"
        )
        _write(DOCS_DIR / f"30_succession_{name.lower().replace(' ', '_')}.md", body, counter)

    for person, ceo_idx, prior, window in BIOS:
        company = sub_name(ceo_idx)
        w_from, w_to = window
        body = (
            f"# Executive Biography: {person}\n\n"
            f"{person} is the CEO of {company}. Before taking the role, "
            f"{person} worked at {prior} from {w_from} to {w_to}.\n"
        )
        slug = person.lower().replace(" ", "_").replace("'", "")
        _write(DOCS_DIR / f"40_bio_{slug}.md", body, counter)

    for product, producer_idx, category, window in PRODUCTS:
        producer = sub_name(producer_idx)
        window_note = f" It has been in production since {window[0]}." if window else ""
        body = (
            f"# Product Note: {product}\n\n"
            f"{product} is a {category} produced by {producer}.{window_note}\n"
        )
        _write(DOCS_DIR / f"50_product_{product.lower().replace(' ', '_')}.md", body, counter)

    for product, producer_idx, category, window in PRODUCT_LINES:
        producer = sub_name(producer_idx)
        body = (
            f"# Product Line Note: {product}\n\n"
            f"{product} is a {category} produced by {producer}. "
            f"The line launched in {window[0]} and remains in production.\n"
        )
        _write(DOCS_DIR / f"51_productline_{product.lower().replace(' ', '_')}.md", body, counter)

    for acq_idx, target, year, note in ACQUISITIONS:
        acquirer = PARENTS[acq_idx][0]
        body = (
            f"# Deal Brief: {acquirer} acquired {target}\n\n"
            f"In {year}, {acquirer} acquired {target}: {note}.\n"
        )
        _write(DOCS_DIR / f"60_acq_{target.lower().replace(' ', '_')}.md", body, counter)

    for a_idx, b_name in COMPETES:
        a = sub_name(a_idx)
        body = (
            f"# Market Note: {a} and {b_name}\n\n"
            f"{a} competes with {b_name} in their shared market.\n"
        )
        _write(DOCS_DIR / f"70_competes_{a.lower().replace(' ', '_')}.md", body, counter)

    # Pad with relationship notes until ≥ 120 docs.
    pad = 0
    while counter[0] < 120:
        a = SUBSIDIARIES[pad % len(SUBSIDIARIES)][0]
        b = SUCCESSIONS[pad % len(SUCCESSIONS)][0]
        body = (
            f"# Relationship Note {pad:03d}: {a} and {b}\n\n"
            f"Analysts track whether {a} and {b} share suppliers, parents, or "
            f"competitive positions across the group structure.\n"
        )
        _write(DOCS_DIR / f"90_note_{pad:03d}.md", body, counter)
        pad += 1


# ── BL-14 conflict drill ────────────────────────────────────────────────────


def bl14_drill() -> dict:
    """Demonstrate the temporal conflict policy end to end (ADR-007)."""
    store = InMemoryGraphStore()
    updater = IncrementalUpdater(store)

    def t(tail: str, conf: float, vf: str | None, vt: str | None) -> Triple:
        return _triple(
            "Elena Marsh",
            "Person",
            "WORKED_AT",
            tail,
            "Company",
            conf,
            "drill",
            valid_from=vf,
            valid_to=vt,
        )

    b1 = updater.apply_batch([t("Aster Bank", 0.9, "2012", "2015")])
    b2 = updater.apply_batch([t("Cobalt Foods", 0.9, "2016", None)])
    # An older overlapping claim: time-first policy keeps the newer incumbent.
    b3 = updater.apply_batch([t("Delta Textiles", 0.9, "2010", None)])

    def edges():
        return sorted(r.tail_name for r in store._relations.values())

    def total(b):
        return b.conflicts_auto + b.conflicts_review + b.conflicts_kept

    drill = {
        "policy": "ADR-007 time-first conflict adjudication",
        "batch1_new_window": {"conflicts": total(b1), "tails": edges()},
        "batch2_disjoint_window": {"conflicts": total(b2), "tails": edges()},
        "batch3_overlapping_older": {"conflicts_kept": b3.conflicts_kept, "tails": edges()},
        "assertions": {
            "disjoint_windows_coexist": len(edges()) == 2,
            "older_overlapping_fact_kept_out": "Delta Textiles" not in edges(),
            "disjoint_edges_both_live": "Aster Bank" in edges() and "Cobalt Foods" in edges(),
        },
    }
    drill["pass"] = all(drill["assertions"].values())
    return drill


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=TRIPLES_PATH)
    args = ap.parse_args()

    counter = [0]
    render_docs(counter)
    triples = build_triples()
    temporal = sum(1 for t in triples if t.valid_from or t.valid_to)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for t in triples:
            f.write(json.dumps(t.model_dump(mode="json"), ensure_ascii=False) + "\n")

    drill = bl14_drill()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "bl14_drill.json").write_text(
        json.dumps(drill, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {counter[0]} documents to {DOCS_DIR}")
    print(f"Wrote {len(triples)} triples ({temporal} temporal) to {args.out}")
    verdict = "PASS" if drill["pass"] else "FAIL"
    print(f"BL-14 drill: {verdict} → reports/temporal_corpus/bl14_drill.json")
    if not drill["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
