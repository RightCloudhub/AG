"""Search-drift metrics using retrieved evidence and optional external LLM labels."""

from __future__ import annotations

import re
import statistics
from collections import Counter

from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.reasoning import QueryStatus

ABSTENTION = re.compile(
    r"未找到|无法确定|无法基于现有知识回答|无法回答|不能确定|"
    r"\bcannot (?:determine|answer|find)\b|\bnot enough (?:evidence|information)\b",
    re.I,
)


def contain_match(prediction: str, gold: str) -> bool:
    return bool(gold.strip()) and gold.casefold().strip() in prediction.casefold()


def retrieved_ids(row: JsonObject) -> set[str]:
    """Use all tool hits, including unused passages, rather than final citations."""
    chain = row.get("chain") or {}
    meta = chain.get("metadata") or row.get("metadata") or {}
    ids = set(meta.get("register_retrieved_ids", []))
    for step in chain.get("steps", []):
        ids.update(step.get("evidence_ids", []))
        for tool in step.get("tool_calls", []):
            ids.update(tool.get("hits", []))
    return {str(rid).removeprefix("chunk:") for rid in ids}


def analyze_row(row: JsonObject, case: JsonObject) -> JsonObject:
    gold = str(case.get("gold_answer", row.get("gold", "")))
    prediction = _prediction(row)
    contain = contain_match(prediction, gold)
    label = row.get("llm_eval")
    if label is not None and not isinstance(label, bool):
        raise ValueError("llm_eval must be a boolean or absent")
    supports, unmatched = gold_chunks(case)
    retrieved = retrieved_ids(row)
    recall = _recall(supports, retrieved, unmatched=unmatched)
    abstained = bool(ABSTENTION.search(prediction)) or row.get("status") == "no_answer"
    false_resolution = bool(_optional_label(row, "false_resolution"))
    posthoc_sufficient = _optional_label(row, "posthoc_sufficient")
    result = {
        "case_id": str(case.get("id", _case_id(row))),
        "contain_match": contain,
        "llm_eval": label,
        "abstained": abstained,
        "evidence_recall": recall,
        "unaligned": unmatched,
        "retrieved_ids": sorted(retrieved),
        "false_resolution": false_resolution,
        "posthoc_sufficient": posthoc_sufficient,
        "premature_answer": (
            posthoc_sufficient is False and row.get("status") == QueryStatus.ANSWERED
        ),
        "by_hop": _hop_recall(case, retrieved),
    }
    result["attribution"] = _attribution(result)
    return result


def _prediction(row: JsonObject) -> str:
    return str(row.get("prediction") or (row.get("chain") or {}).get("answer") or "")


def _case_id(row: JsonObject) -> str:
    return str(row.get("case_id") or row.get("id") or "")


def _optional_label(row: JsonObject, key: str) -> bool | None:
    value = row.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean or absent")
    return value


def gold_chunks(case: JsonObject) -> tuple[set[str], bool]:
    """Legacy gold entity/relation names are not passage ids (e.g. g2_all)."""
    meta = case.get("metadata", {})
    raw = case.get("gold_evidence", [])
    explicit = meta.get("aligned_gold_chunks")
    if explicit is None:
        explicit = [rid for rid in raw if str(rid).startswith("chunk:")]
        unmatched = len(explicit) != len(raw)
    else:
        unmatched = False
    ids = {str(rid).removeprefix("chunk:") for rid in explicit}
    return ids, unmatched or bool(meta.get("unaligned_gold_passages"))


def _recall(supports: set[str], retrieved: set[str], *, unmatched: bool) -> float | None:
    return len(supports & retrieved) / len(supports) if supports and not unmatched else None


def _attribution(result: JsonObject) -> str:
    label = result["llm_eval"]
    if result["contain_match"] and label is False:
        return "judge_error"
    if label is True or (label is None and result["contain_match"]):
        return "correct"
    if result["abstained"]:
        return "abstention"
    if any(
        (result["false_resolution"], result["premature_answer"], result["evidence_recall"] == 1)
    ):
        return "reasoning_error"
    if result["evidence_recall"] is not None:
        return "evidence_missing"
    return "unaligned_or_unlabeled"


def _hop_recall(case: JsonObject, retrieved: set[str]) -> JsonObject:
    by_hop = case.get("metadata", {}).get("gold_evidence_by_hop", {})
    result = {}
    for hop, ids in by_hop.items():
        gold = {str(rid).removeprefix("chunk:") for rid in ids}
        result[str(hop)] = len(gold & retrieved) / len(gold) if gold else None
    return result


def summarize(rows: list[JsonObject], cases: dict[str, JsonObject]) -> JsonObject:
    items = [analyze_row(r, cases.get(_case_id(r), {})) for r in rows]
    return {
        "total": len(items),
        **_answer_metrics(items),
        **_evidence_metrics(items),
        "by_attribution": dict(Counter(i["attribution"] for i in items)),
        "mean_tokens": _mean_cost(rows, "tokens"),
        "mean_llm_calls": _mean_cost(rows, "llm_calls"),
        "mean_rounds": statistics.fmean(_rounds(r) for r in rows) if rows else 0,
        "cases": items,
    }


def _answer_metrics(items: list[JsonObject]) -> JsonObject:
    answered = [i for i in items if not i["abstained"]]
    judged = [i for i in items if i["llm_eval"] is not None]
    return {
        "contain_match": _accuracy(items, "contain_match"),
        "llm_eval_accuracy": _accuracy(judged, "llm_eval"),
        "llm_eval_n": len(judged),
        "selective_accuracy": _accuracy(answered, "contain_match"),
        "selective_llm_accuracy": _accuracy(
            [i for i in answered if i["llm_eval"] is not None], "llm_eval"
        ),
        "abstention_rate": 1 - len(answered) / len(items) if items else 0,
    }


def _evidence_metrics(items: list[JsonObject]) -> JsonObject:
    recalls = [i["evidence_recall"] for i in items if i["evidence_recall"] is not None]
    return {
        "evidence_recall": statistics.fmean(recalls) if recalls else None,
        "evidence_recall_n": len(recalls),
        "unaligned_n": sum(i["unaligned"] for i in items),
    }


def _accuracy(items: list[JsonObject], key: str) -> float | None:
    return sum(bool(i[key]) for i in items) / len(items) if items else None


def _mean_cost(rows: list[JsonObject], key: str) -> float:
    return statistics.fmean((r.get("cost") or {}).get(key, 0) for r in rows) if rows else 0


def _rounds(row: JsonObject) -> int:
    meta = (row.get("chain") or {}).get("metadata") or row.get("metadata") or {}
    return int(meta.get("register_rounds") or row.get("hop_count") or 0)


def reachable_unasked(base: JsonObject, candidate: JsonObject, gold_ids: set[str]) -> list[str]:
    """Newly retrieved gold demonstrates reachability; absence proves nothing."""
    gold = {rid.removeprefix("chunk:") for rid in gold_ids}
    return sorted((retrieved_ids(candidate) - retrieved_ids(base)) & gold)


def summarize_repeats(reports: list[JsonObject]) -> JsonObject:
    values = [r["contain_match"] for r in reports if r.get("contain_match") is not None]
    return {
        "runs": len(values),
        "contain_match_mean": statistics.fmean(values) if values else None,
        "contain_match_variance": statistics.variance(values) if len(values) > 1 else 0,
    }
