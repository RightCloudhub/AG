"""Path templates that emit multi-hop gold EvalCases from an edge index."""

from __future__ import annotations

from collections.abc import Callable

from agentic_graphrag.eval.cases import CaseCategory, EvalCase
from agentic_graphrag.eval.gold_index import Edge


def emit_2hop_cases(
    edges: list[Edge],
    out_adj: dict[str, list[Edge]],
    in_adj: dict[str, list[Edge]],
    add: Callable[[EvalCase], None],
    *,
    max_2hop: int,
) -> int:
    n2 = 0
    # CEO of parent of company: X -SUBSIDIARY_OF-> P <-CEO_OF- person
    for e in edges:
        if n2 >= max_2hop:
            break
        if e.relation not in {"SUBSIDIARY_OF", "PARENT_OF"}:
            continue
        if e.relation == "SUBSIDIARY_OF":
            child, parent = e.head, e.tail
            path = [child, "SUBSIDIARY_OF", parent]
        else:
            parent, child = e.head, e.tail
            path = [child, "PARENT_OF", parent]
        # CEO of parent
        for e2 in in_adj.get(parent, []):
            if e2.relation != "CEO_OF":
                continue
            person = e2.head
            add(
                EvalCase(
                    id=f"gen-2hop-ceo-parent-{n2:03d}",
                    question=f"Who is the CEO of the parent company of {child}?",
                    gold_answer=person,
                    hops=2,
                    category=CaseCategory.HOP2,
                    gold_path=path + ["CEO_OF", person],
                    gold_evidence=[child, parent, person, "PARENT_OF", "CEO_OF"],
                )
            )
            n2 += 1
            break

    # prior employers of CEO of company
    for e in edges:
        if n2 >= max_2hop:
            break
        if e.relation != "CEO_OF":
            continue
        person, company = e.head, e.tail
        employers = [x.tail for x in out_adj.get(person, []) if x.relation == "WORKED_AT"]
        if not employers:
            continue
        gold = " and ".join(dict.fromkeys(employers))
        add(
            EvalCase(
                id=f"gen-2hop-ceo-worked-{n2:03d}",
                question=f"Which companies did the CEO of {company} previously work at?",
                gold_answer=gold,
                hops=2,
                category=CaseCategory.HOP2,
                gold_path=[company, "CEO_OF", person, "WORKED_AT", gold],
                gold_evidence=[company, person, *employers],
            )
        )
        n2 += 1

    # competitor of producer of product
    for e in edges:
        if n2 >= max_2hop:
            break
        if e.relation != "PRODUCES":
            continue
        producer, product = e.head, e.tail
        comps = [
            x.tail if x.head == producer else x.head
            for x in out_adj.get(producer, []) + in_adj.get(producer, [])
            if x.relation == "COMPETES_WITH"
        ]
        if not comps:
            continue
        add(
            EvalCase(
                id=f"gen-2hop-compet-prod-{n2:03d}",
                question=f"Which company competes with the producer of {product}?",
                gold_answer=comps[0],
                hops=2,
                category=CaseCategory.HOP2,
                gold_path=[product, "PRODUCES", producer, "COMPETES_WITH", comps[0]],
                gold_evidence=[product, producer, comps[0]],
            )
        )
        n2 += 1
    return n2


def emit_3hop_cases(
    edges: list[Edge],
    out_adj: dict[str, list[Edge]],
    in_adj: dict[str, list[Edge]],
    add: Callable[[EvalCase], None],
    *,
    max_3hop: int,
) -> int:
    n3 = 0
    # CEO of competitor of producer of product
    for e in edges:
        if n3 >= max_3hop:
            break
        if e.relation != "PRODUCES":
            continue
        producer, product = e.head, e.tail
        comps = [
            x.tail if x.head == producer else x.head
            for x in out_adj.get(producer, []) + in_adj.get(producer, [])
            if x.relation == "COMPETES_WITH"
        ]
        for comp in comps:
            ceos = [x.head for x in in_adj.get(comp, []) if x.relation == "CEO_OF"]
            if not ceos:
                continue
            add(
                EvalCase(
                    id=f"gen-3hop-ceo-compet-prod-{n3:03d}",
                    question=f"Who is the CEO of the competitor of the producer of {product}?",
                    gold_answer=ceos[0],
                    hops=3,
                    category=CaseCategory.HOP3,
                    gold_path=[
                        product,
                        "PRODUCES",
                        producer,
                        "COMPETES_WITH",
                        comp,
                        "CEO_OF",
                        ceos[0],
                    ],
                    gold_evidence=[product, producer, comp, ceos[0]],
                )
            )
            n3 += 1
            if n3 >= max_3hop:
                break

    # parent of producer of product (extended with CEO → 3 hop)
    for e in edges:
        if n3 >= max_3hop:
            break
        if e.relation != "PRODUCES":
            continue
        producer, product = e.head, e.tail
        parents = [
            x.tail if x.relation == "SUBSIDIARY_OF" else x.head
            for x in out_adj.get(producer, []) + in_adj.get(producer, [])
            if x.relation in {"SUBSIDIARY_OF", "PARENT_OF"}
            and (
                (x.relation == "SUBSIDIARY_OF" and x.head == producer)
                or (x.relation == "PARENT_OF" and x.tail == producer)
            )
        ]
        for parent in parents:
            ceos = [x.head for x in in_adj.get(parent, []) if x.relation == "CEO_OF"]
            if not ceos:
                continue
            add(
                EvalCase(
                    id=f"gen-3hop-ceo-parent-prod-{n3:03d}",
                    question=(
                        f"Who is the CEO of the parent company of the producer of {product}?"
                    ),
                    gold_answer=ceos[0],
                    hops=3,
                    category=CaseCategory.HOP3,
                    gold_path=[
                        product,
                        "PRODUCES",
                        producer,
                        "PARENT_OF",
                        parent,
                        "CEO_OF",
                        ceos[0],
                    ],
                    gold_evidence=[product, producer, parent, ceos[0]],
                )
            )
            n3 += 1
            break
    return n3


def emit_open_cases(
    edges: list[Edge],
    add: Callable[[EvalCase], None],
    *,
    max_open: int,
) -> int:
    n_open = 0
    companies = sorted(
        {e.head for e in edges if e.relation in {"COMPETES_WITH", "PARENT_OF"}}
        | {e.tail for e in edges if e.relation in {"COMPETES_WITH", "SUBSIDIARY_OF"}}
    )
    for i, a in enumerate(companies):
        if n_open >= max_open:
            break
        for b in companies[i + 1 :]:
            if n_open >= max_open:
                break
            # direct competes
            linked = any(
                (x.head == a and x.tail == b) or (x.head == b and x.tail == a)
                for x in edges
                if x.relation == "COMPETES_WITH"
            )
            if not linked:
                continue
            add(
                EvalCase(
                    id=f"gen-open-path-{n_open:03d}",
                    question=f"What is the relationship chain between {a} and {b}?",
                    gold_answer=f"{a} COMPETES_WITH {b}",
                    hops=1,
                    category=CaseCategory.OPEN,
                    gold_path=[a, "COMPETES_WITH", b],
                    gold_evidence=[a, b, "COMPETES_WITH"],
                )
            )
            n_open += 1
    return n_open


def emit_no_answer_cases(
    edges: list[Edge],
    add: Callable[[EvalCase], None],
    *,
    max_no_answer: int,
) -> int:
    known = {e.head for e in edges} | {e.tail for e in edges}
    phantoms = [
        "Zephyr Dynamics LLC",
        "Nimbus Quantum Corp",
        "Aurora Lattice Inc",
        "Phantom Holdings Group",
        "Void Systems AG",
        "Mirage Capital Partners",
        "Echo Robotics GmbH",
        "Nullspace Energy",
        "Chimera BioTech",
        "Specter Logistics Co",
        "Umbra Semiconductor",
        "Fata Morgana AI",
        "Wraith Ventures",
        "Shade Industrial",
        "Gossamer Cloud Ltd",
        "Twilight Aggregates",
        "Hollow Point Materials",
        "Driftwood Mining",
        "Paper Tiger Motors",
        "Invisible Ink Media",
        "Moonbeam Cement",
        "Sandcastle Defense",
    ]
    n_na = 0
    for name in phantoms:
        if n_na >= max_no_answer:
            break
        if name in known:
            continue
        add(
            EvalCase(
                id=f"gen-noans-{n_na:03d}",
                question=f"Who is the CEO of the parent company of {name}?",
                gold_answer="no answer",
                hops=0,
                category=CaseCategory.NO_ANSWER,
                gold_path=[],
                gold_evidence=[],
                notes="entity absent from seed graph",
            )
        )
        n_na += 1
    return n_na
