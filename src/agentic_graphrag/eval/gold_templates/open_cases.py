"""Open-ended gold case path templates."""

from __future__ import annotations

from agentic_graphrag.eval.cases import CaseCategory, EvalCase
from agentic_graphrag.eval.gold_templates.context import EmitContext
from agentic_graphrag.eval.gold_templates.helpers import (
    MIN_EVENT_PARTICIPANTS,
    REL_COMPETES_WITH,
    REL_PARTICIPATED_IN,
    REL_SUPPLIES,
    company_entities,
    competes_linked,
    event_participants,
    join_names,
)

DEFAULT_MAX_OPEN = 30
HOPS_OPEN = 1
_ID_OPEN_PATH = "gen-open-path"
_ID_OPEN_EVENT = "gen-open-event"
_ID_OPEN_SHARED_SUP = "gen-open-shared-sup"


def emit_open_cases(ctx: EmitContext) -> int:
    """Emit open-path / event / shared-supplier cases into ``ctx``."""
    _emit_competes_path(ctx)
    if ctx.has_in_adj:
        _emit_event_participants(ctx)
    if ctx.has_out_adj:
        _emit_shared_supplier(ctx)
    return ctx.count


def _emit_competes_path(ctx: EmitContext) -> None:
    companies = company_entities(ctx.edges)
    for i, a in enumerate(companies):
        if ctx.full():
            return
        for b in companies[i + 1 :]:
            if ctx.full():
                return
            if not competes_linked(ctx.edges, a, b):
                continue
            case = EvalCase(
                id=ctx.case_id(_ID_OPEN_PATH),
                question=f"What is the relationship chain between {a} and {b}?",
                gold_answer=f"{a} {REL_COMPETES_WITH} {b}",
                hops=HOPS_OPEN,
                category=CaseCategory.OPEN,
                gold_path=[a, REL_COMPETES_WITH, b],
                gold_evidence=[a, b, REL_COMPETES_WITH],
                metadata={"template": "competes_path"},
            )
            ctx.try_add(case)


def _emit_event_participants(ctx: EmitContext) -> None:
    events = event_participants(ctx.edges)
    for event, orgs in sorted(events.items()):
        if ctx.full():
            return
        uniq = list(dict.fromkeys(orgs))
        if len(uniq) < MIN_EVENT_PARTICIPANTS:
            continue
        case = EvalCase(
            id=ctx.case_id(_ID_OPEN_EVENT),
            question=f"Which companies participated in {event}?",
            gold_answer=join_names(uniq),
            hops=HOPS_OPEN,
            category=CaseCategory.OPEN,
            gold_path=[uniq[0], REL_PARTICIPATED_IN, event],
            gold_evidence=[event, *uniq, REL_PARTICIPATED_IN],
            metadata={"template": "event_participants"},
        )
        ctx.try_add(case)


def _emit_shared_supplier(ctx: EmitContext) -> None:
    """Legacy: always increments count after add (even if deduped)."""
    for e in ctx.edges:
        if ctx.full():
            return
        if e.relation != REL_SUPPLIES:
            continue
        supplier, company = e.head, e.tail
        others = [
            x.tail
            for x in ctx.out_adj.get(supplier, [])
            if x.relation == REL_SUPPLIES and x.tail != company
        ]
        if not others:
            continue
        case = EvalCase(
            id=ctx.case_id(_ID_OPEN_SHARED_SUP),
            question=f"Which companies share the supplier {supplier} with {company}?",
            gold_answer=join_names(others),
            hops=HOPS_OPEN,
            category=CaseCategory.OPEN,
            gold_path=[supplier, REL_SUPPLIES, company],
            gold_evidence=[supplier, company, *others],
            metadata={"template": "shared_supplier"},
        )
        ctx.add(case)
        ctx.count += 1
