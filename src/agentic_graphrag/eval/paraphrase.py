"""Validated paraphrase groups and answer consistency, using the existing scorer."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from agentic_graphrag.eval.cases import EvalCase
from agentic_graphrag.eval.scoring import score_pair
from agentic_graphrag.eval.scoring_strict import EntityMatcher, gold_entity_set

VARIANTS = ("original", "synonym", "reordered", "chinese", "typo", "casual")


def group_cases(cases: list[EvalCase]) -> dict[str, dict[str, EvalCase]]:
    """Reject incomplete/duplicate groups and changed labels before running queries."""
    groups: dict[str, dict[str, EvalCase]] = defaultdict(dict)
    for case in cases:
        original = str(case.metadata.get("original_id", ""))
        tag = str(case.metadata.get("variant", ""))
        if not original or tag not in VARIANTS or case.id != f"{original}::{tag}":
            raise ValueError(f"Invalid paraphrase identity: {case.id}")
        if tag in groups[original]:
            raise ValueError(f"Duplicate variant: {case.id}")
        groups[original][tag] = case
    if not groups:
        raise ValueError("No paraphrase groups")
    for original, forms in groups.items():
        _validate_group(original, forms)
    return dict(groups)


def _validate_group(original: str, forms: dict[str, EvalCase]) -> None:
    if set(forms) != set(VARIANTS):
        raise ValueError(f"Incomplete group: {original}")
    base = forms["original"]
    for case in forms.values():
        if (case.gold_answer, case.gold_path, case.gold_evidence, case.hops, case.category) != (
            base.gold_answer,
            base.gold_path,
            base.gold_evidence,
            base.hops,
            base.category,
        ):
            raise ValueError(f"Changed gold labels: {case.id}")


def _text_key(text: str) -> str:
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _outcome(
    case: EvalCase, row: dict[str, Any], *, base: EvalCase, matcher: EntityMatcher
) -> dict[str, Any]:
    prediction = str(row.get("prediction") or "")
    failed = bool(row.get("error")) or row.get("status") == "error" or not row
    correct = not failed and bool(score_pair(prediction, case.gold_answer)["correct"])
    gold = gold_entity_set({"gold": case.gold_answer}, matcher)
    # Use the ORIGINAL mentions for all forms: a typo/translation must not alter exclusions.
    anchors = matcher.find(base.question) - (gold or set())
    named = matcher.find(prediction) - anchors
    exact = (not failed and named == gold) if gold is not None else None
    signature = sorted(named) if gold is not None else [_text_key(prediction)]
    # Open-path answers must include the relation as well as both endpoint names;
    # the legacy overlap scorer alone can accept a different relation between them.
    gold_tokens = set(_text_key(case.gold_answer).replace("_", " ").split())
    pred_tokens = set(_text_key(prediction).replace("_", " ").split())
    return {
        "case_id": case.id,
        "correct": correct,
        "exact_set": exact,
        "signature": signature,
        "complete_gold": exact if gold is not None else gold_tokens <= pred_tokens,
        "error": failed,
    }


def summarize(
    cases: list[EvalCase], rows: list[dict[str, Any]], *, known_entities: list[str]
) -> dict[str, Any]:
    """Missing/error rows count as wrong; consensus never rewards consistent failures.

    Entity answers require the exact gold entity set for consistency. Other answers
    require identical normalized text containing all gold tokens and six successful scores.
    Legacy accuracy is reported alongside exact-set accuracy, never substituted for it.
    """
    groups = group_cases(cases)
    by_id = {str(row["case_id"]): row for row in rows}
    if len(by_id) != len(rows) or set(by_id) - {case.id for case in cases}:
        raise ValueError("Duplicate or unknown prediction IDs")
    matcher = EntityMatcher(known_entities)
    outcomes: dict[str, list[dict[str, Any]]] = {tag: [] for tag in VARIANTS}
    consistent = all_correct = 0
    for forms in groups.values():
        group = []
        for tag in VARIANTS:
            case = forms[tag]
            result = _outcome(case, by_id.get(case.id, {}), base=forms["original"], matcher=matcher)
            outcomes[tag].append(result)
            group.append(result)
        all_correct += all(r["correct"] for r in group)
        consistent += _consistent(group)
    return {
        "groups": len(groups),
        "total": len(cases),
        "by_variant": {tag: _rates(values) for tag, values in outcomes.items()},
        "all_correct_groups": all_correct,
        "consistent_correct_groups": consistent,
        "consistency": consistent / len(groups),
    }


def _consistent(group: list[dict[str, Any]]) -> bool:
    return all(r["correct"] and r["complete_gold"] for r in group) and all(
        r["signature"] == group[0]["signature"] for r in group
    )


def _rates(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(r["correct"] for r in outcomes)
    exact = [r["exact_set"] for r in outcomes if r["exact_set"] is not None]
    return {
        "correct": correct,
        "total": len(outcomes),
        "accuracy": correct / len(outcomes),
        "errors": sum(r["error"] for r in outcomes),
        "exact_set_correct": sum(exact),
        "exact_set_total": len(exact),
        "exact_set_accuracy": sum(exact) / len(exact) if exact else None,
    }
