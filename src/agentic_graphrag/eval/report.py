"""One-click comparison report from run artifacts (P2-EV-04 / FR-OP-04).

Reads existing JSONL run files (agentic + baseline), computes Accuracy,
evidence Recall, latency, and cost deltas, and writes a JSON (+ optional
Markdown) comparison report. Does **not** re-run systems — execution is
deferred to ``agr-run-cases`` / ``agr-run-baseline``.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.eval.scoring import score_pair


def _percentile(values: list[float], p: float) -> float:
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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _latency_ms(row: dict[str, Any]) -> float:
    if "latency_ms" in row and row["latency_ms"] is not None:
        return float(row["latency_ms"])
    cost = row.get("cost") or {}
    return float(cost.get("latency_ms") or 0)


def _cost_tokens(row: dict[str, Any]) -> int:
    cost = row.get("cost") or {}
    return int(cost.get("tokens") or 0)


def _cost_calls(row: dict[str, Any]) -> int:
    cost = row.get("cost") or {}
    return int(cost.get("llm_calls") or 0)


def _gold_evidence_items(row: dict[str, Any], cases_by_id: dict[str, dict]) -> list[str]:
    """Gold evidence tokens: gold_path nodes/relations or case gold_evidence."""
    cid = row.get("case_id") or row.get("id")
    case = cases_by_id.get(str(cid) if cid is not None else "", {})
    items: list[str] = []
    for key in ("gold_evidence", "gold_path"):
        raw = case.get(key) or row.get(key)
        if isinstance(raw, list):
            items.extend(str(x) for x in raw if x)
        elif isinstance(raw, str) and raw.strip():
            items.append(raw.strip())
    return items


def _predicted_evidence_blob(row: dict[str, Any]) -> str:
    """Flatten prediction + chain evidence for recall matching."""
    parts: list[str] = [str(row.get("prediction") or "")]
    chain = row.get("chain") or {}
    if isinstance(chain, dict):
        for claim in chain.get("claims") or []:
            if isinstance(claim, dict):
                parts.append(str(claim.get("text") or ""))
                parts.extend(str(x) for x in (claim.get("evidence_ids") or []))
        for step in chain.get("steps") or []:
            if isinstance(step, dict):
                parts.append(str(step.get("conclusion") or ""))
                parts.append(str(step.get("sub_question") or ""))
                parts.extend(str(x) for x in (step.get("evidence_ids") or []))
                for tc in step.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        parts.extend(str(x) for x in (tc.get("hits") or []))
        parts.extend(str(x) for x in (chain.get("explored_paths") or []))
    parts.extend(str(x) for x in (row.get("explored_paths") or []))
    return " ".join(parts).lower()


def evidence_recall_for_row(
    row: dict[str, Any],
    cases_by_id: dict[str, dict],
    *,
    min_hops: int = 2,
) -> float | None:
    """Fraction of gold evidence items mentioned in the chain (AC-2 style).

    Returns None when the case is skipped (e.g. hops < min_hops or no gold).
    """
    cid = str(row.get("case_id") or row.get("id") or "")
    case = cases_by_id.get(cid, {})
    hops = int(case.get("hops") or row.get("hop_count") or row.get("hops") or 0)
    if hops and hops < min_hops:
        return None
    gold_items = _gold_evidence_items(row, cases_by_id)
    if not gold_items:
        return None
    blob = _predicted_evidence_blob(row)
    hits = 0
    for item in gold_items:
        token = str(item).lower().strip()
        if not token:
            continue
        # Relation labels like SUBSIDIARY_OF/PARENT_OF — any segment counts
        segments = [s.strip() for s in token.replace("/", " ").split() if s.strip()]
        if any(seg.lower() in blob for seg in segments):
            hits += 1
    return hits / len(gold_items) if gold_items else None


def fabrication_rate(rows: list[dict[str, Any]]) -> float:
    """Share of rows with answered status but no cited claims (AC-7 proxy)."""
    if not rows:
        return 0.0
    bad = 0
    counted = 0
    for row in rows:
        status = str(row.get("status") or "").lower()
        if status in {"no_answer", ""}:
            continue
        counted += 1
        chain = row.get("chain") or {}
        claims = chain.get("claims") if isinstance(chain, dict) else None
        if not claims:
            # baseline extractive may omit chain claims — treat unbound answer as risk
            if status == "answered" and not (row.get("prediction") or "").startswith("无法"):
                bad += 1
            continue
        if any(not (c.get("evidence_ids") if isinstance(c, dict) else True) for c in claims):
            bad += 1
    return (bad / counted) if counted else 0.0


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


def score_system_rows(
    rows: list[dict[str, Any]],
    *,
    system: str,
    cases_by_id: dict[str, dict] | None = None,
) -> SystemMetrics:
    cases_by_id = cases_by_id or {}
    metrics = SystemMetrics(system=system)
    latencies: list[float] = []
    tokens: list[int] = []
    calls: list[int] = []
    recalls: list[float] = []
    hop_stats: dict[str, dict[str, int]] = {}

    for row in rows:
        gold = row.get("gold") or row.get("gold_answer") or ""
        pred = row.get("prediction") or ""
        if row.get("error") and not pred:
            s = {"correct": False, "score": 0.0, "method": "error"}
        else:
            s = score_pair(str(pred), str(gold))
        metrics.total += 1
        if s["correct"]:
            metrics.correct += 1

        lat = _latency_ms(row)
        latencies.append(lat)
        tok = _cost_tokens(row)
        cl = _cost_calls(row)
        tokens.append(tok)
        calls.append(cl)

        rec = evidence_recall_for_row(row, cases_by_id)
        if rec is not None:
            recalls.append(rec)

        cid = str(row.get("case_id") or row.get("id") or "")
        case = cases_by_id.get(cid, {})
        hops = str(case.get("hops") or row.get("hop_count") or row.get("hops") or "unknown")
        bucket = hop_stats.setdefault(hops, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if s["correct"]:
            bucket["correct"] += 1

        metrics.cases.append(
            {
                "case_id": cid,
                "correct": s["correct"],
                "score": s["score"],
                "method": s["method"],
                "gold": gold,
                "prediction": str(pred)[:300],
                "latency_ms": lat,
                "evidence_recall": rec,
                "hops": hops,
            }
        )

    metrics.accuracy = (metrics.correct / metrics.total) if metrics.total else 0.0
    metrics.latency_p50_ms = _percentile(latencies, 50)
    metrics.latency_p95_ms = _percentile(latencies, 95)
    metrics.latency_mean_ms = statistics.fmean(latencies) if latencies else 0.0
    metrics.cost_tokens_total = sum(tokens)
    metrics.cost_tokens_mean = statistics.fmean(tokens) if tokens else 0.0
    metrics.cost_llm_calls_total = sum(calls)
    metrics.cost_llm_calls_mean = statistics.fmean(calls) if calls else 0.0
    metrics.evidence_recall = statistics.fmean(recalls) if recalls else None
    metrics.evidence_recall_n = len(recalls)
    metrics.fabrication_rate = fabrication_rate(rows)
    metrics.by_hops = {
        h: {
            "total": v["total"],
            "correct": v["correct"],
            "accuracy": round(v["correct"] / v["total"], 4) if v["total"] else 0.0,
        }
        for h, v in sorted(hop_stats.items(), key=lambda kv: kv[0])
    }
    return metrics


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


def build_comparison_report(
    *,
    agentic_path: Path,
    baseline_path: Path | None = None,
    cases_path: Path | None = None,
) -> dict[str, Any]:
    cases_by_id = load_cases(cases_path) if cases_path else {}
    agentic_rows = _load_jsonl(agentic_path)
    agentic = score_system_rows(agentic_rows, system="agentic", cases_by_id=cases_by_id)

    systems: dict[str, Any] = {"agentic": agentic.to_dict()}
    delta: dict[str, Any] = {}
    if baseline_path and baseline_path.exists():
        baseline_rows = _load_jsonl(baseline_path)
        baseline = score_system_rows(baseline_rows, system="baseline", cases_by_id=cases_by_id)
        systems["baseline"] = baseline.to_dict()
        delta = {
            "accuracy_pp": round((agentic.accuracy - baseline.accuracy) * 100, 2),
            "evidence_recall_pp": (
                round((agentic.evidence_recall - baseline.evidence_recall) * 100, 2)
                if agentic.evidence_recall is not None and baseline.evidence_recall is not None
                else None
            ),
            "latency_p50_ms_delta": round(agentic.latency_p50_ms - baseline.latency_p50_ms, 2),
            "latency_p95_ms_delta": round(agentic.latency_p95_ms - baseline.latency_p95_ms, 2),
            "cost_tokens_mean_delta": round(
                agentic.cost_tokens_mean - baseline.cost_tokens_mean, 2
            ),
            "cost_llm_calls_mean_delta": round(
                agentic.cost_llm_calls_mean - baseline.cost_llm_calls_mean, 2
            ),
        }

    badcases = [c for c in agentic.cases if not c["correct"]]
    return {
        "schema_version": "1.0.0",
        "task": "P2-EV-04",
        "inputs": {
            "agentic": str(agentic_path),
            "baseline": str(baseline_path) if baseline_path else None,
            "cases": str(cases_path) if cases_path else None,
        },
        "systems": systems,
        "delta_agentic_minus_baseline": delta,
        "badcases": badcases,
        "summary": {
            "agentic_accuracy_pct": systems["agentic"]["accuracy_pct"],
            "baseline_accuracy_pct": (
                systems["baseline"]["accuracy_pct"] if "baseline" in systems else None
            ),
            "accuracy_pp": delta.get("accuracy_pp"),
            "agentic_evidence_recall": systems["agentic"]["evidence_recall"],
            "agentic_latency_p50_ms": systems["agentic"]["latency_p50_ms"],
            "agentic_latency_p95_ms": systems["agentic"]["latency_p95_ms"],
            "agentic_cost_tokens_mean": systems["agentic"]["cost_tokens_mean"],
            "fabrication_rate": systems["agentic"]["fabrication_rate"],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report.get("summary") or {}
    lines = [
        "# Evaluation comparison report (P2-EV-04)",
        "",
        "## Summary",
        "",
        f"- Agentic accuracy: **{s.get('agentic_accuracy_pct')}%**",
        f"- Baseline accuracy: **{s.get('baseline_accuracy_pct')}%**",
        f"- Accuracy delta (pp): **{s.get('accuracy_pp')}**",
        f"- Agentic evidence recall: **{s.get('agentic_evidence_recall')}**",
        f"- Agentic latency P50/P95 (ms): **{s.get('agentic_latency_p50_ms')}** / "
        f"**{s.get('agentic_latency_p95_ms')}**",
        f"- Agentic mean tokens: **{s.get('agentic_cost_tokens_mean')}**",
        f"- Fabrication rate: **{s.get('fabrication_rate')}**",
        "",
        "## Systems",
        "",
    ]
    for name, metrics in (report.get("systems") or {}).items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- total/correct: {metrics.get('total')}/{metrics.get('correct')}")
        lines.append(f"- accuracy: {metrics.get('accuracy_pct')}%")
        lines.append(f"- evidence_recall: {metrics.get('evidence_recall')}")
        p50, p95 = metrics.get("latency_p50_ms"), metrics.get("latency_p95_ms")
        lines.append(f"- latency P50/P95: {p50} / {p95} ms")
        lines.append(
            f"- cost mean tokens/calls: {metrics.get('cost_tokens_mean')} / "
            f"{metrics.get('cost_llm_calls_mean')}"
        )
        lines.append(f"- by_hops: `{json.dumps(metrics.get('by_hops') or {}, ensure_ascii=False)}`")
        lines.append("")
    bad = report.get("badcases") or []
    lines.append(f"## Badcases ({len(bad)})")
    lines.append("")
    for b in bad[:50]:
        lines.append(
            f"- `{b.get('case_id')}` gold=`{b.get('gold')}` pred=`{b.get('prediction')}`"
        )
    lines.append("")
    return "\n".join(lines)


def write_comparison_report(
    report: dict[str, Any],
    out_dir: Path,
    *,
    stem: str = "eval_comparison",
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "md": md_path}
