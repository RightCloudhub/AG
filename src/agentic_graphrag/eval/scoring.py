"""Score predictions against gold answers for G1 (not hardcoded rates)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


def _norm(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _tokens(text: str) -> set[str]:
    stop = {
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
    return {t for t in _norm(text).split() if t and t not in stop and len(t) > 1}


def score_pair(prediction: str, gold: str) -> dict:
    """Token-overlap + substring match score for one case."""
    pred = prediction or ""
    gold = gold or ""
    pn, gn = _norm(pred), _norm(gold)
    if not gn:
        return {"correct": False, "score": 0.0, "method": "empty_gold"}
    if not pn:
        return {"correct": False, "score": 0.0, "method": "empty_pred"}

    # Exact / containment
    if gn in pn or pn in gn:
        return {"correct": True, "score": 1.0, "method": "containment"}

    # All significant gold tokens present in prediction
    gt, pt = _tokens(gold), _tokens(pred)
    if not gt:
        return {"correct": False, "score": 0.0, "method": "no_tokens"}
    overlap = gt & pt
    ratio = len(overlap) / len(gt)
    # Also check multi-word gold aliases split on ' and ' / ','
    aliases = re.split(r"\s+and\s+|,\s*", gold, flags=re.I)
    alias_hits = 0
    for a in aliases:
        an = _norm(a)
        if an and an in pn:
            alias_hits += 1
    if len(aliases) > 1 and alias_hits == len(aliases):
        return {"correct": True, "score": 1.0, "method": "all_aliases"}
    if len(aliases) > 1 and alias_hits >= max(1, len(aliases) - 0):
        # require all for strict; partial aliases give partial credit
        pass

    correct = ratio >= 0.6 or (alias_hits >= 1 and ratio >= 0.4)
    # Special: gold "Yes" 
    if gn in {"yes", "no"} and gn in pn.split():
        correct = True
        ratio = 1.0
    return {
        "correct": correct,
        "score": round(ratio, 4),
        "method": "token_overlap",
        "overlap": sorted(overlap),
    }


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
        row = json.loads(line)
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
            continue
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
                "prediction": pred[:300],
            }
        )
    return report


def write_accuracy_summary(report_path: str | Path, out_path: str | Path) -> AccuracyReport:
    acc = score_report_file(report_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(acc.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return acc
