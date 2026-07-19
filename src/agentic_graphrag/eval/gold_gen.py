"""Deterministic gold-case generator from seed triples (P2-EV-01).

No LLM: walks the synthetic graph (seed/pilot triples) and templates
natural-language multi-hop questions with gold answers + path evidence.

Index: :mod:`agentic_graphrag.eval.gold_index`.
Templates: :mod:`agentic_graphrag.eval.gold_templates`.
"""

from __future__ import annotations

from agentic_graphrag.eval.cases import EvalCase
from agentic_graphrag.eval.gold_index import index_triples
from agentic_graphrag.eval.gold_templates import (
    emit_2hop_cases,
    emit_3hop_cases,
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

    def add(case: EvalCase) -> None:
        key = case.question.lower().strip()
        if key in seen_q:
            return
        seen_q.add(key)
        cases.append(case)

    emit_2hop_cases(edges, out_adj, in_adj, add, max_2hop=max_2hop)
    emit_3hop_cases(edges, out_adj, in_adj, add, max_3hop=max_3hop)
    emit_open_cases(edges, add, max_open=max_open)
    if include_no_answer:
        emit_no_answer_cases(edges, add, max_no_answer=max_no_answer)

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
