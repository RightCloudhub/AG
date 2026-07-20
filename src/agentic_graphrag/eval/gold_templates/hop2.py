"""2-hop gold case path templates."""

from __future__ import annotations

from agentic_graphrag.eval.cases import CaseCategory, EvalCase
from agentic_graphrag.eval.gold_templates.context import EmitContext
from agentic_graphrag.eval.gold_templates.helpers import (
    REL_CEO_OF,
    REL_COMPETES_WITH,
    REL_PARENT_OF,
    REL_PRODUCES,
    REL_SUBSIDIARY_OF,
    REL_SUPPLIES,
    REL_SUPPLIES_FOR,
    REL_WORKED_AT,
    ceos_of,
    child_parent_of,
    competitors_of,
    employers_of,
    join_names,
    parents_of,
)

HOPS_2 = 2
_ID_CEO_PARENT = "gen-2hop-ceo-parent"
_ID_CEO_WORKED = "gen-2hop-ceo-worked"
_ID_COMPET_PROD = "gen-2hop-compet-prod"
_ID_PARENT_PROD = "gen-2hop-parent-prod"
_ID_SUPPLIER_DUAL = "gen-2hop-supplier-dual"
_ID_CEO_OF_PRIOR = "gen-2hop-ceo-of-prior"
_ID_PARENT_COMPET = "gen-2hop-parent-compet"


def emit_2hop_cases(ctx: EmitContext) -> int:
    """Emit 2-hop cases into ``ctx`` until ``ctx.max_n``."""
    _emit_ceo_of_parent(ctx)
    _emit_ceo_prior_employers(ctx)
    _emit_competitor_of_producer(ctx)
    _emit_parent_of_producer(ctx)
    _emit_dual_supplier(ctx)
    _emit_ceo_of_prior_employer(ctx)
    _emit_parent_of_competitor(ctx)
    return ctx.count


def _emit_ceo_of_parent(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        pair = child_parent_of(e)
        if pair is None:
            continue
        child, parent = pair
        path = (
            [child, REL_SUBSIDIARY_OF, parent]
            if e.relation == REL_SUBSIDIARY_OF
            else [child, REL_PARENT_OF, parent]
        )
        for e2 in ctx.in_adj.get(parent, []):
            if e2.relation != REL_CEO_OF:
                continue
            person = e2.head
            case = EvalCase(
                id=ctx.case_id(_ID_CEO_PARENT),
                question=f"Who is the CEO of the parent company of {child}?",
                gold_answer=person,
                hops=HOPS_2,
                category=CaseCategory.HOP2,
                gold_path=path + [REL_CEO_OF, person],
                gold_evidence=[child, parent, person, REL_PARENT_OF, REL_CEO_OF],
                metadata={"template": "ceo_of_parent"},
            )
            ctx.try_add(case)
            break


def _emit_ceo_prior_employers(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_CEO_OF:
            continue
        person, company = e.head, e.tail
        employers = employers_of(person, ctx.out_adj)
        if not employers:
            continue
        gold = join_names(employers)
        case = EvalCase(
            id=ctx.case_id(_ID_CEO_WORKED),
            question=f"Which companies did the CEO of {company} previously work at?",
            gold_answer=gold,
            hops=HOPS_2,
            category=CaseCategory.HOP2,
            gold_path=[company, REL_CEO_OF, person, REL_WORKED_AT, gold],
            gold_evidence=[company, person, *employers],
            metadata={"template": "ceo_prior_employers"},
        )
        ctx.try_add(case)


def _emit_competitor_of_producer(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_PRODUCES:
            continue
        producer, product = e.head, e.tail
        comps = competitors_of(producer, ctx.out_adj, ctx.in_adj)
        if not comps:
            continue
        case = EvalCase(
            id=ctx.case_id(_ID_COMPET_PROD),
            question=f"Which company competes with the producer of {product}?",
            gold_answer=comps[0],
            hops=HOPS_2,
            category=CaseCategory.HOP2,
            gold_path=[product, REL_PRODUCES, producer, REL_COMPETES_WITH, comps[0]],
            gold_evidence=[product, producer, comps[0]],
            metadata={"template": "competitor_of_producer"},
        )
        ctx.try_add(case)


def _emit_parent_of_producer(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_PRODUCES:
            continue
        producer, product = e.head, e.tail
        parents = parents_of(producer, ctx.out_adj, ctx.in_adj)
        if not parents:
            continue
        case = EvalCase(
            id=ctx.case_id(_ID_PARENT_PROD),
            question=f"What is the parent company of the producer of {product}?",
            gold_answer=parents[0],
            hops=HOPS_2,
            category=CaseCategory.HOP2,
            gold_path=[product, REL_PRODUCES, producer, REL_PARENT_OF, parents[0]],
            gold_evidence=[product, producer, parents[0]],
            metadata={"template": "parent_of_producer"},
        )
        ctx.try_add(case)


def _emit_dual_supplier(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_SUPPLIES_FOR:
            continue
        supplier, product = e.head, e.tail
        company_supplies = [
            x.tail for x in ctx.out_adj.get(supplier, []) if x.relation == REL_SUPPLIES
        ]
        if not company_supplies:
            continue
        case = EvalCase(
            id=ctx.case_id(_ID_SUPPLIER_DUAL),
            question=(
                f"Which supplier for {product} also supplies {company_supplies[0]}?"
            ),
            gold_answer=supplier,
            hops=HOPS_2,
            category=CaseCategory.HOP2,
            gold_path=[
                product,
                REL_SUPPLIES_FOR,
                supplier,
                REL_SUPPLIES,
                company_supplies[0],
            ],
            gold_evidence=[product, supplier, company_supplies[0]],
            metadata={"template": "dual_supplier"},
        )
        ctx.try_add(case)


def _emit_ceo_of_prior_employer(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_WORKED_AT:
            continue
        person, company = e.head, e.tail
        ceos = ceos_of(company, ctx.in_adj)
        if not ceos:
            continue
        if any(c.lower() == person.lower() for c in ceos):
            continue
        case = EvalCase(
            id=ctx.case_id(_ID_CEO_OF_PRIOR),
            question=f"Who is the CEO of a company where {person} previously worked?",
            gold_answer=ceos[0],
            hops=HOPS_2,
            category=CaseCategory.HOP2,
            gold_path=[person, REL_WORKED_AT, company, REL_CEO_OF, ceos[0]],
            gold_evidence=[person, company, ceos[0]],
            metadata={"template": "ceo_of_prior_employer"},
        )
        ctx.try_add(case)


def _emit_parent_of_competitor(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_COMPETES_WITH:
            continue
        a, b = e.head, e.tail
        parents = parents_of(b, ctx.out_adj, ctx.in_adj)
        if not parents:
            continue
        case = EvalCase(
            id=ctx.case_id(_ID_PARENT_COMPET),
            question=f"What is the parent company of a competitor of {a}?",
            gold_answer=parents[0],
            hops=HOPS_2,
            category=CaseCategory.HOP2,
            gold_path=[a, REL_COMPETES_WITH, b, REL_PARENT_OF, parents[0]],
            gold_evidence=[a, b, parents[0]],
            metadata={"template": "parent_of_competitor"},
        )
        ctx.try_add(case)
