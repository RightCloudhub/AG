"""Score predictions against gold answers for G1 (not hardcoded rates)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_TOKEN_RATIO = 0.6
_ALIAS_PARTIAL_RATIO = 0.4
_PRED_PREVIEW = 300
_NO_ANSWER_GOLDS = frozenset(
    {
        "no answer",
        "n/a",
        "none",
        "unknown",
        "无法回答",
        "no_answer",
    }
)
# Honest abstention phrases (EN + system Chinese fallback from ReasoningChain.honest_fallback)
_ABSTAIN_MARKERS = (
    "无法基于现有知识回答",
    "无法回答",
    "no evidence retrieved",
    "cannot answer",
    "can't answer",
    "unable to answer",
    "insufficient evidence",
    "no matching relation",
    "not available in the provided evidence",
    "not provided in the available evidence",
    "no information available",
)
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "at",
        "for",
        "is",
        "are",
        "was",
        "were",
        "with",
        "from",
        "by",
        "on",
        "as",
        "based",
        "retrieved",
        "evidence",
    }
)


def _norm(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t and t not in _STOP and len(t) > 1}


def score_pair(prediction: str, gold: str) -> dict:
    """Token-overlap + substring match score for one case."""
    pred = prediction or ""
    gold = gold or ""
    pn, gn = _norm(pred), _norm(gold)
    early = _early_score(pn, gn, pred=pred, gold=gold)
    if early is not None:
        return early
    # yes/no: word-boundary only (avoid "no" ⊂ "know", "yes" ⊂ "yesterday")
    if gn in {"yes", "no"}:
        if _token_boundary_match(pn, gn):
            return {"correct": True, "score": 1.0, "method": "yes_no"}
        return _overlap_score(gold=gold, pn=pn, gn=gn)
    if _containment_match(pn, gn):
        return {"correct": True, "score": 1.0, "method": "containment"}
    return _overlap_score(gold=gold, pn=pn, gn=gn)


def is_no_answer_gold(gold: str) -> bool:
    return _norm(gold or "") in _NO_ANSWER_GOLDS


def is_honest_abstention(prediction: str) -> bool:
    """True when the system abstained rather than inventing an answer."""
    raw = (prediction or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if low in _NO_ANSWER_GOLDS:
        return True
    return any(m.lower() in low or m in raw for m in _ABSTAIN_MARKERS)


def _token_boundary_match(haystack: str, needle: str) -> bool:
    """True when needle appears as a whole token in haystack."""
    if not needle or not haystack:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))


def _containment_match(pn: str, gn: str) -> bool:
    """Substring match with word boundaries for short gold answers."""
    if not gn or not pn:
        return False
    # Short single-token golds: require token boundaries (blocks no⊂know).
    gold_tokens = gn.split()
    if len(gold_tokens) == 1 and len(gn) <= 4:
        return _token_boundary_match(pn, gn) or _token_boundary_match(gn, pn)
    if gn in pn or pn in gn:
        return True
    return False


def _early_score(pn: str, gn: str, *, pred: str = "", gold: str = "") -> dict | None:
    if not gn:
        return {"correct": False, "score": 0.0, "method": "empty_gold"}
    if is_no_answer_gold(gold or gn) and is_honest_abstention(pred or pn):
        return {"correct": True, "score": 1.0, "method": "no_answer_abstain"}
    if not pn:
        return {"correct": False, "score": 0.0, "method": "empty_pred"}
    return None


def _overlap_score(*, gold: str, pn: str, gn: str) -> dict:
    gt, pt = _tokens(gold), _tokens(pn)
    if not gt:
        # Single-char / stopword golds (e.g. "no") may yield empty token sets.
        if gn in {"yes", "no"} and _token_boundary_match(pn, gn):
            return {"correct": True, "score": 1.0, "method": "yes_no"}
        return {"correct": False, "score": 0.0, "method": "no_tokens"}
    overlap = gt & pt
    ratio = len(overlap) / len(gt)
    alias_hits, n_aliases = _alias_hits(gold, pn)
    if n_aliases > 1 and alias_hits == n_aliases:
        return {"correct": True, "score": 1.0, "method": "all_aliases"}
    correct = ratio >= _TOKEN_RATIO or (alias_hits >= 1 and ratio >= _ALIAS_PARTIAL_RATIO)
    if gn in {"yes", "no"} and _token_boundary_match(pn, gn):
        correct, ratio = True, 1.0
    return {
        "correct": correct,
        "score": round(ratio, 4),
        "method": "token_overlap",
        "overlap": sorted(overlap),
    }


def _alias_hits(gold: str, pn: str) -> tuple[int, int]:
    aliases = re.split(r"\s+and\s+|,\s*", gold, flags=re.I)
    hits = sum(1 for a in aliases if (an := _norm(a)) and an in pn)
    return hits, len(aliases)


@dataclass
class AccuracyReport:
    total: int = 0
    correct: int = 0
    cases: list[dict] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return (self.correct / self.total) if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "accuracy_pct": round(self.accuracy * 100, 2),
            "cases": self.cases,
        }


def score_report_file(report_path: str | Path) -> AccuracyReport:
    path = Path(report_path)
    report = AccuracyReport()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        _score_report_row(json.loads(line), report)
    return report


def _score_report_row(row: dict, report: AccuracyReport) -> None:
    if row.get("error") and not row.get("prediction"):
        report.total += 1
        report.cases.append(
            {
                "case_id": row.get("case_id"),
                "correct": False,
                "score": 0.0,
                "method": "error",
                "gold": row.get("gold"),
                "prediction": "",
            }
        )
        return
    gold = row.get("gold") or ""
    pred = row.get("prediction") or ""
    s = score_pair(pred, gold)
    report.total += 1
    if s["correct"]:
        report.correct += 1
    report.cases.append(
        {
            "case_id": row.get("case_id"),
            "correct": s["correct"],
            "score": s["score"],
            "method": s["method"],
            "gold": gold,
            "prediction": pred[:_PRED_PREVIEW],
        }
    )


def write_accuracy_summary(report_path: str | Path, out_path: str | Path) -> AccuracyReport:
    acc = score_report_file(report_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(acc.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return acc
