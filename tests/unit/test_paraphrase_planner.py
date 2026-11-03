"""Fixed dev groups expose lexical planning failures without any live LLM."""

from pathlib import Path

import pytest

from agentic_graphrag.agent.planner import plan_offline
from agentic_graphrag.cli.cases_run import known_entity_names, load_seed_triples
from agentic_graphrag.eval.cases import load_cases
from agentic_graphrag.eval.paraphrase import group_cases

ROOT = Path(__file__).resolve().parents[2]
GROUPS = group_cases(load_cases(ROOT / "evals/datasets/paraphrase.jsonl"))
NAMES = known_entity_names(load_seed_triples(ROOT / "data/processed/pilot_triples.jsonl"))
SELECTED = (
    "g2-open-0001",
    "g2-2hop-0001",
    "g2-2hop-0005",
    "g2-2hop-0041",
    "g2-2hop-0045",
    "g2-2hop-0088",
    "g2-2hop-0090",
    "g2-3hop-0001",
    "g2-3hop-0006",
    "g2-3hop-0047",
)
# Explicit debt, not dynamic xfail-on-any-assertion. Strict XPASS requires removing resolved debt.
GAPS = {
    ("g2-2hop-0045", "synonym"): "trailing employed ... in the past is not parsed",
    ("g2-2hop-0090", "synonym"): "verb rivals is not a leading frame",
    ("g2-2hop-0005", "typo"): "relation typo parnet is not recognized",
    ("g2-2hop-0090", "typo"): "relation typo prodcuer is not recognized",
}


def _parameters():
    for ident in SELECTED:
        for tag in ("synonym", "typo"):
            reason = GAPS.get((ident, tag))
            marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
            yield pytest.param(ident, tag, marks=marks, id=f"{ident}-{tag}")


@pytest.mark.parametrize(("ident", "tag"), list(_parameters()))
def test_planner_preserves_relation_chain_and_anchor(ident, tag):
    forms = GROUPS[ident]
    original = plan_offline(forms["original"].question, known_entities=NAMES)
    variant = plan_offline(forms[tag].question, known_entities=NAMES)
    assert len(variant) == len(original)
    # Canonical sub-question text encodes relation direction AND the seed entity;
    # counting nodes alone would miss wrong anchors and reversed relations.
    assert [(s.text, s.depends_on, s.is_placeholder) for s in variant] == [
        (s.text, s.depends_on, s.is_placeholder) for s in original
    ]
