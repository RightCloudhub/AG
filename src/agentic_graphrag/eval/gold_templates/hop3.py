"""3-hop gold case path templates."""

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
    grandparents_of,
    join_names,
    parents_of,
    producers_of_product,
    suppliers_of,
)

HOPS_3 = 3
_ID_CEO_COMPET_PROD = "gen-3hop-ceo-compet-prod"
_ID_CEO_PARENT_PROD = "gen-3hop-ceo-parent-prod"
_ID_WORKED_PARENT_CEO = "gen-3hop-worked-parent-ceo"
_ID_CEO_SUP_COMPET = "gen-3hop-ceo-sup-compet"
_ID_CEO_GRANDPARENT = "gen-3hop-ceo-grandparent"
_ID_CEO_PROD_FROM_SUP = "gen-3hop-ceo-prod-from-sup"
_ID_CEO_COMPET_PRIOR = "gen-3hop-ceo-compet-prior"


def emit_3hop_cases(ctx: EmitContext) -> int:
    """Emit 3-hop cases into ``ctx`` until ``ctx.max_n``."""
    _emit_ceo_competitor_producer(ctx)
    _emit_ceo_parent_producer(ctx)
    _emit_prior_of_parent_ceo(ctx)
    _emit_ceo_supplier_of_competitor(ctx)
    _emit_ceo_grandparent(ctx)
    _emit_ceo_of_producer_via_supply(ctx)
    _emit_ceo_competitor_of_prior_employer(ctx)
    return ctx.count


def _case(ctx: EmitContext, *, prefix: str, question: str, gold: str,
          path: list[str], evidence: list[str], template: str) -> EvalCase:
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


def _emit_ceo_competitor_producer(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_PRODUCES:
            continue
        producer, product = e.head, e.tail
        for comp in competitors_of(producer, ctx.out_adj, ctx.in_adj):
            if ctx.full():
                return
            ceos = ceos_of(comp, ctx.in_adj)
            if not ceos:
                continue
            ctx.try_add(
                _case(
                    ctx,
                    prefix=_ID_CEO_COMPET_PROD,
                    question=(
                        f"Who is the CEO of the competitor of the producer of {product}?"
                    ),
                    gold=ceos[0],
                    path=[
                        product, REL_PRODUCES, producer, REL_COMPETES_WITH,
                        comp, REL_CEO_OF, ceos[0],
                    ],
                    evidence=[product, producer, comp, ceos[0]],
                    template="ceo_competitor_producer",
                )
            )


def _emit_ceo_parent_producer(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_PRODUCES:
            continue
        producer, product = e.head, e.tail
        for parent in parents_of(producer, ctx.out_adj, ctx.in_adj):
            ceos = ceos_of(parent, ctx.in_adj)
            if not ceos:
                continue
            ctx.try_add(
                _case(
                    ctx,
                    prefix=_ID_CEO_PARENT_PROD,
                    question=(
                        f"Who is the CEO of the parent company of the "
                        f"producer of {product}?"
                    ),
                    gold=ceos[0],
                    path=[
                        product, REL_PRODUCES, producer, REL_PARENT_OF,
                        parent, REL_CEO_OF, ceos[0],
                    ],
                    evidence=[product, producer, parent, ceos[0]],
                    template="ceo_parent_producer",
                )
            )
            break


def _emit_prior_of_parent_ceo(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        pair = child_parent_of(e)
        if pair is None:
            continue
        child, parent = pair
        for person in ceos_of(parent, ctx.in_adj):
            employers = employers_of(person, ctx.out_adj)
            if not employers:
                continue
            gold = join_names(employers)
            ctx.try_add(
                _case(
                    ctx,
                    prefix=_ID_WORKED_PARENT_CEO,
                    question=(
                        f"Which companies did the CEO of the parent of {child} "
                        f"previously work at?"
                    ),
                    gold=gold,
                    path=[
                        child, REL_PARENT_OF, parent, REL_CEO_OF,
                        person, REL_WORKED_AT, gold,
                    ],
                    evidence=[child, parent, person, *employers],
                    template="prior_of_parent_ceo",
                )
            )
            break


def _emit_ceo_supplier_of_competitor(ctx: EmitContext) -> None:
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_COMPETES_WITH:
            continue
        a, b = e.head, e.tail
        for sup in suppliers_of(b, ctx.in_adj):
            ceos = ceos_of(sup, ctx.in_adj)
            if not ceos:
                continue
            ctx.try_add(
                _case(
                    ctx,
                    prefix=_ID_CEO_SUP_COMPET,
                    question=f"Who is the CEO of a supplier of a competitor of {a}?",
                    gold=ceos[0],
                    path=[
                        a, REL_COMPETES_WITH, b, REL_SUPPLIES,
                        sup, REL_CEO_OF, ceos[0],
                    ],
                    evidence=[a, b, sup, ceos[0]],
                    template="ceo_supplier_of_competitor",
                )
            )
            break


def _emit_ceo_grandparent(ctx: EmitContext) -> None:
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
                        f"Who is the CEO of the parent company of {parent} "
                        f"(owner of {child})?"
                    ),
                    gold=ceos[0],
                    path=[
                        child, REL_SUBSIDIARY_OF, parent, REL_SUBSIDIARY_OF,
                        gp, REL_CEO_OF, ceos[0],
                    ],
                    evidence=[child, parent, gp, ceos[0]],
                    template="ceo_grandparent",
                )
            )
            break


def _emit_ceo_of_producer_via_supply(ctx: EmitContext) -> None:
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
                    supplier, REL_SUPPLIES_FOR, product, REL_PRODUCES,
                    producer, REL_CEO_OF, ceos[0],
                ],
                evidence=[supplier, product, producer, ceos[0]],
                template="ceo_of_producer_via_supply",
            )
            ctx.add(case)
            ctx.count += 1
            break


def _emit_ceo_competitor_of_prior_employer(ctx: EmitContext) -> None:
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
                        f"Who is the CEO of a competitor of a company "
                        f"where {person} worked?"
                    ),
                    gold=ceos[0],
                    path=[
                        person, REL_WORKED_AT, company, REL_COMPETES_WITH,
                        comp, REL_CEO_OF, ceos[0],
                    ],
                    evidence=[person, company, comp, ceos[0]],
                    template="ceo_competitor_of_prior_employer",
                )
            )
            break
