"""Deterministic gold-case generator from triples (P2-EV-01/02).

No LLM: walks the graph (seed/pilot triples) and templates multi-hop
questions with gold answers + path evidence. Builds stratified ≥200 sets
and optional guardrail cases (AC-6, not in the 200).
"""

from __future__ import annotations

from collections import defaultdict

from agentic_graphrag.eval.cases import CaseCategory, EvalCase, StratificationSpec
from agentic_graphrag.eval.gold_index import index_triples
from agentic_graphrag.eval.gold_templates import (
    emit_2hop_cases,
    emit_3hop_cases,
    emit_guardrail_cases,
    emit_no_answer_cases,
    emit_open_cases,
)
from agentic_graphrag.knowledge.schema_check import Triple


def generate_gold_cases(
    triples: list[Triple],
    *,
    max_2hop: int = 90,
    max_3hop: int = 60,
    max_open: int = 30,
    max_no_answer: int = 20,
    include_no_answer: bool = True,
) -> list[EvalCase]:
    """Generate stratified cases from triples via path templates."""
    edges, out_adj, in_adj = index_triples(triples)
    cases: list[EvalCase] = []
    seen_q: set[str] = set()

    def add(case: EvalCase) -> bool:
        """Return True when the case was kept (unique question)."""
        key = case.question.lower().strip()
        if not key or key in seen_q:
            return False
        seen_q.add(key)
        cases.append(case)
        return True

    emit_2hop_cases(edges, out_adj, in_adj, add, max_2hop=max_2hop)
    emit_3hop_cases(edges, out_adj, in_adj, add, max_3hop=max_3hop)
    emit_open_cases(edges, out_adj, in_adj, add, max_open=max_open)
    if include_no_answer:
        emit_no_answer_cases(edges, add, max_no_answer=max_no_answer)

    return cases


def generate_stratified_eval_set(
    triples: list[Triple],
    spec: StratificationSpec | None = None,
    *,
    oversample: float = 3.0,
) -> list[EvalCase]:
    """Generate and trim to G2 mix targets (default ≥200 with 90/60/30/20)."""
    spec = spec or StratificationSpec()
    # Oversample then pick first N per category for stable deterministic order.
    # Caps are "successful unique questions"; templates skip duplicates without
    # burning the budget (add() returns bool).
    raw = generate_gold_cases(
        triples,
        max_2hop=max(spec.min_2hop, int(spec.min_2hop * oversample)),
        max_3hop=max(spec.min_3hop, int(spec.min_3hop * oversample)),
        max_open=max(spec.min_open, int(spec.min_open * oversample)),
        max_no_answer=max(spec.min_no_answer, int(spec.min_no_answer * oversample)),
        include_no_answer=True,
    )
    buckets: dict[CaseCategory, list[EvalCase]] = defaultdict(list)
    for c in raw:
        buckets[c.resolved_category()].append(c)

    targets = {
        CaseCategory.HOP2: spec.min_2hop,
        CaseCategory.HOP3: spec.min_3hop,
        CaseCategory.OPEN: spec.min_open,
        CaseCategory.NO_ANSWER: spec.min_no_answer,
    }
    selected: list[EvalCase] = []
    for cat, need in targets.items():
        pool = buckets.get(cat, [])
        if len(pool) < need:
            # take all; caller should validate
            selected.extend(pool)
        else:
            selected.extend(pool[:need])

    # stable id remap for the curated set
    out: list[EvalCase] = []
    counters: dict[str, int] = defaultdict(int)
    for c in selected:
        cat = c.resolved_category().value
        counters[cat] += 1
        new_id = f"g2-{cat}-{counters[cat]:04d}"
        out.append(
            c.model_copy(
                update={
                    "id": new_id,
                    "metadata": {
                        **(c.metadata or {}),
                        "source_id": c.id,
                        "label_source": "deterministic_path_template",
                        "annotation_status": "auto_gold",
                    },
                }
            )
        )
    return out


def generate_guardrail_set(*, max_n: int = 25) -> list[EvalCase]:
    cases: list[EvalCase] = []
    seen: set[str] = set()

    def add(case: EvalCase) -> bool:
        key = case.question.lower().strip() or case.id
        if key in seen:
            return False
        seen.add(key)
        cases.append(case)
        return True

    emit_guardrail_cases(add, max_n=max_n)
    return cases


def generate_and_write(
    triples: list[Triple],
    out_path: str,
    **kwargs: object,
) -> list[EvalCase]:
    from agentic_graphrag.eval.cases import dump_cases

    cases = generate_gold_cases(triples, **kwargs)  # type: ignore[arg-type]
    dump_cases(cases, out_path)
    return cases
