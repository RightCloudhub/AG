"""Metric computation for evaluation run artifacts (P2-EV-04 / FR-OP-04).

Accuracy, evidence recall, latency, cost, fabrication rate. Report rendering
lives in :mod:`agentic_graphrag.eval.report`.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.eval.metrics_evidence import (
    evidence_recall_for_row,
    fabrication_rate,
    gold_evidence_items,
    predicted_evidence_blob,
)
from agentic_graphrag.eval.scoring import score_pair

_P50 = 50.0
_P95 = 95.0
_PRED_PREVIEW = 300

__all__ = [
    "SystemMetrics",
    "cost_calls",
    "cost_tokens",
    "evidence_recall_for_row",
    "fabrication_rate",
    "gold_evidence_items",
    "latency_ms",
    "load_cases",
    "load_jsonl",
    "percentile",
    "predicted_evidence_blob",
    "score_system_rows",
]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return float(ordered[f])
    return float(ordered[f] + (ordered[c] - ordered[f]) * (k - f))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def latency_ms(row: dict[str, Any]) -> float:
    if "latency_ms" in row and row["latency_ms"] is not None:
        return float(row["latency_ms"])
    cost = row.get("cost") or {}
    return float(cost.get("latency_ms") or 0)


def cost_tokens(row: dict[str, Any]) -> int:
    cost = row.get("cost") or {}
    return int(cost.get("tokens") or 0)


def cost_calls(row: dict[str, Any]) -> int:
    cost = row.get("cost") or {}
    return int(cost.get("llm_calls") or 0)


@dataclass
class SystemMetrics:
    system: str
    total: int = 0
    correct: int = 0
    accuracy: float = 0.0
    evidence_recall: float | None = None
    evidence_recall_n: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_mean_ms: float = 0.0
    cost_tokens_total: int = 0
    cost_tokens_mean: float = 0.0
    cost_llm_calls_total: int = 0
    cost_llm_calls_mean: float = 0.0
    fabrication_rate: float = 0.0
    by_hops: dict[str, dict[str, Any]] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "total": self.total,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "accuracy_pct": round(self.accuracy * 100, 2),
            "evidence_recall": (
                round(self.evidence_recall, 4) if self.evidence_recall is not None else None
            ),
            "evidence_recall_n": self.evidence_recall_n,
            "latency_p50_ms": round(self.latency_p50_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "latency_mean_ms": round(self.latency_mean_ms, 2),
            "cost_tokens_total": self.cost_tokens_total,
            "cost_tokens_mean": round(self.cost_tokens_mean, 2),
            "cost_llm_calls_total": self.cost_llm_calls_total,
            "cost_llm_calls_mean": round(self.cost_llm_calls_mean, 2),
            "fabrication_rate": round(self.fabrication_rate, 4),
            "by_hops": self.by_hops,
            "cases": self.cases,
        }


@dataclass
class _ScoreAccum:
    latencies: list[float] = field(default_factory=list)
    tokens: list[int] = field(default_factory=list)
    calls: list[int] = field(default_factory=list)
    recalls: list[float] = field(default_factory=list)
    hop_stats: dict[str, dict[str, int]] = field(default_factory=dict)


def score_system_rows(
    rows: list[dict[str, Any]],
    *,
    system: str,
    cases_by_id: dict[str, dict] | None = None,
) -> SystemMetrics:
    cases_by_id = cases_by_id or {}
    metrics = SystemMetrics(system=system)
    acc = _ScoreAccum()
    for row in rows:
        _score_one_row(row, metrics, cases_by_id=cases_by_id, acc=acc)
    _finalize_metrics(metrics, rows, acc)
    return metrics


def _score_one_row(
    row: dict[str, Any],
    metrics: SystemMetrics,
    *,
    cases_by_id: dict[str, dict],
    acc: _ScoreAccum,
) -> None:
    gold = row.get("gold") or row.get("gold_answer") or ""
    pred = row.get("prediction") or ""
    s = _pair_score(row, pred, gold)
    metrics.total += 1
    if s["correct"]:
        metrics.correct += 1
    lat = latency_ms(row)
    acc.latencies.append(lat)
    acc.tokens.append(cost_tokens(row))
    acc.calls.append(cost_calls(row))
    rec = evidence_recall_for_row(row, cases_by_id)
    if rec is not None:
        acc.recalls.append(rec)
    hops = _row_hops(row, cases_by_id)
    _bump_hop(acc, hops, s["correct"])
    metrics.cases.append(
        {
            "case_id": str(row.get("case_id") or row.get("id") or ""),
            "correct": s["correct"],
            "score": s["score"],
            "method": s["method"],
            "gold": gold,
            "prediction": str(pred)[:_PRED_PREVIEW],
            "latency_ms": lat,
            "evidence_recall": rec,
            "hops": hops,
        }
    )


def _pair_score(row: dict[str, Any], pred: Any, gold: Any) -> dict[str, Any]:
    if row.get("error") and not pred:
        return {"correct": False, "score": 0.0, "method": "error"}
    return score_pair(str(pred), str(gold))


def _row_hops(row: dict[str, Any], cases_by_id: dict[str, dict]) -> str:
    cid = str(row.get("case_id") or row.get("id") or "")
    case = cases_by_id.get(cid, {})
    return str(case.get("hops") or row.get("hop_count") or row.get("hops") or "unknown")


def _bump_hop(acc: _ScoreAccum, hops: str, correct: bool) -> None:
    bucket = acc.hop_stats.setdefault(hops, {"total": 0, "correct": 0})
    bucket["total"] += 1
    if correct:
        bucket["correct"] += 1


def _finalize_metrics(
    metrics: SystemMetrics,
    rows: list[dict[str, Any]],
    acc: _ScoreAccum,
) -> None:
    metrics.accuracy = (metrics.correct / metrics.total) if metrics.total else 0.0
    metrics.latency_p50_ms = percentile(acc.latencies, _P50)
    metrics.latency_p95_ms = percentile(acc.latencies, _P95)
    metrics.latency_mean_ms = statistics.fmean(acc.latencies) if acc.latencies else 0.0
    metrics.cost_tokens_total = sum(acc.tokens)
    metrics.cost_tokens_mean = statistics.fmean(acc.tokens) if acc.tokens else 0.0
    metrics.cost_llm_calls_total = sum(acc.calls)
    metrics.cost_llm_calls_mean = statistics.fmean(acc.calls) if acc.calls else 0.0
    metrics.evidence_recall = statistics.fmean(acc.recalls) if acc.recalls else None
    metrics.evidence_recall_n = len(acc.recalls)
    metrics.fabrication_rate = fabrication_rate(rows)
    metrics.by_hops = {
        h: {
            "total": v["total"],
            "correct": v["correct"],
            "accuracy": round(v["correct"] / v["total"], 4) if v["total"] else 0.0,
        }
        for h, v in sorted(acc.hop_stats.items(), key=lambda kv: kv[0])
    }


def load_cases(path: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    if not path.exists():
        return by_id
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cid = str(row.get("id") or row.get("case_id") or "")
        if cid:
            by_id[cid] = row
    return by_id
