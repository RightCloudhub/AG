"""Focused multi-hop extractive dispatcher (rule order is significant)."""

from __future__ import annotations

from collections.abc import Callable

from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView
from agentic_graphrag.generation.offline_heuristics.mentions import mentions_in_question
from agentic_graphrag.generation.offline_heuristics.rules_market import (
    rule_ceo_of_competitor,
    rule_competitor,
)
from agentic_graphrag.generation.offline_heuristics.rules_misc import (
    rule_event,
    rule_relationship_path,
    rule_shared_connections,
)
from agentic_graphrag.generation.offline_heuristics.rules_org import (
    rule_logistics,
    rule_parent_of_producer,
    rule_parent_owns,
    rule_subsidiary_yes_no,
)
from agentic_graphrag.generation.offline_heuristics.rules_people import (
    rule_both_orion,
    rule_ceo_of_company,
    rule_ceo_of_parent,
    rule_meridian_executives,
    rule_meridian_helix_ceo,
    rule_prior_employers,
)
from agentic_graphrag.generation.offline_heuristics.rules_supply import (
    rule_products,
    rule_suppliers,
)

RuleFn = Callable[..., str | None]

# Same order as the original monolithic focused_extract (order matters).
_RULES: tuple[RuleFn, ...] = (
    rule_both_orion,
    rule_subsidiary_yes_no,
    rule_meridian_helix_ceo,
    rule_meridian_executives,
    rule_prior_employers,
    rule_ceo_of_parent,
    rule_competitor,
    rule_ceo_of_competitor,
    rule_ceo_of_company,
    rule_parent_of_producer,
    rule_parent_owns,
    rule_logistics,
    rule_suppliers,
    rule_products,
    rule_event,
    rule_relationship_path,
    rule_shared_connections,
)


def focused_extract(
    question: str, edges: list[tuple[str, str, str]], texts: list[str]
) -> str | None:
    q = question.lower()
    ents = mentions_in_question(question)
    view = EdgeView(edges)
    for rule in _RULES:
        hit = rule(q, ents, view, texts=texts)
        if hit is not None:
            return hit
    return None
