"""Additional 3-hop gold emitters (split for file-size gate)."""

from __future__ import annotations

from agentic_graphrag.eval.cases import CaseCategory, EvalCase
from agentic_graphrag.eval.gold_templates.context import EmitContext
from agentic_graphrag.eval.gold_templates.helpers import (
    REL_CEO_OF,
    REL_COMPETES_WITH,
    REL_PRODUCES,
    REL_SUBSIDIARY_OF,
    REL_SUPPLIES_FOR,
    REL_WORKED_AT,
    ceos_of,
    competitors_of,
    grandparents_of,
    producers_of_product,
)

HOPS_3 = 3
_ID_CEO_GRANDPARENT = "gen-3hop-ceo-grandparent"
_ID_CEO_PROD_FROM_SUP = "gen-3hop-ceo-prod-from-sup"
_ID_CEO_COMPET_PRIOR = "gen-3hop-ceo-compet-prior"


def _case(
    ctx: EmitContext,
    *,
    prefix: str,
    question: str,
    gold: str,
    path: list[str],
    evidence: list[str],
    template: str,
) -> EvalCase:
    return EvalCase(
        id=ctx.case_id(prefix),
        question=question,
        gold_answer=gold,
        hops=HOPS_3,
        category=CaseCategory.HOP3,
        gold_path=path,
        gold_evidence=evidence,
        metadata={"template": template},
    )


def emit_ceo_grandparent(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_SUBSIDIARY_OF:
            continue
        child, parent = e.head, e.tail
        for gp in grandparents_of(parent, ctx.out_adj, ctx.in_adj):
            if gp == child or gp == parent:
                continue
            ceos = ceos_of(gp, ctx.in_adj)
            if not ceos:
                continue
            ctx.try_add(
                _case(
                    ctx,
                    prefix=_ID_CEO_GRANDPARENT,
                    question=(
                        f"Who is the CEO of the parent company of {parent} (owner of {child})?"
                    ),
                    gold=ceos[0],
                    path=[
                        child,
                        REL_SUBSIDIARY_OF,
                        parent,
                        REL_SUBSIDIARY_OF,
                        gp,
                        REL_CEO_OF,
                        ceos[0],
                    ],
                    evidence=[child, parent, gp, ceos[0]],
                    template="ceo_grandparent",
                )
            )
            break


def emit_ceo_of_producer_via_supply(ctx: EmitContext) -> None:
    """Legacy: always increments count after add (even if deduped)."""
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_SUPPLIES_FOR:
            continue
        supplier, product = e.head, e.tail
        for producer in producers_of_product(product, ctx.edges):
            ceos = ceos_of(producer, ctx.in_adj)
            if not ceos:
                continue
            case = _case(
                ctx,
                prefix=_ID_CEO_PROD_FROM_SUP,
                question=(
                    f"Who is the CEO of the company that produces a product "
                    f"supplied-for by {supplier} ({product})?"
                ),
                gold=ceos[0],
                path=[
                    supplier,
                    REL_SUPPLIES_FOR,
                    product,
                    REL_PRODUCES,
                    producer,
                    REL_CEO_OF,
                    ceos[0],
                ],
                evidence=[supplier, product, producer, ceos[0]],
                template="ceo_of_producer_via_supply",
            )
            ctx.add(case)
            ctx.count += 1
            break


def emit_ceo_competitor_of_prior_employer(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_WORKED_AT:
            continue
        person, company = e.head, e.tail
        for comp in competitors_of(company, ctx.out_adj, ctx.in_adj):
            ceos = ceos_of(comp, ctx.in_adj)
            if not ceos:
                continue
            ctx.try_add(
                _case(
                    ctx,
                    prefix=_ID_CEO_COMPET_PRIOR,
                    question=(
                        f"Who is the CEO of a competitor of a company where {person} worked?"
                    ),
                    gold=ceos[0],
                    path=[
                        person,
                        REL_WORKED_AT,
                        company,
                        REL_COMPETES_WITH,
                        comp,
                        REL_CEO_OF,
                        ceos[0],
                    ],
                    evidence=[person, company, comp, ceos[0]],
                    template="ceo_competitor_of_prior_employer",
                )
            )
            break
