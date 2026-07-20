"""P3-EV offline regression helpers (heldout / triage A-B / incremental drill).

Produces structured JSON for G3 materials; does not claim live AC pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.eval.metrics import load_jsonl, score_system_rows
from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

SCHEMA_VERSION = "1.0.0"
TASK_HELD_OUT = "P3-EV-01"
TASK_TRIAGE = "P3-EV-02"
TASK_INCREMENTAL = "P3-EV-03-incremental"
ACCURACY_PP_TARGET = 25.0
RECALL_TARGET = 0.85
TRIAGE_LOSS_MAX_PP = 2.0


@dataclass
class SplitScore:
    system: str
    accuracy: float
    evidence_recall: float | None
    latency_p95_ms: float
    n: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "accuracy": self.accuracy,
            "accuracy_pct": round(self.accuracy * 100, 2),
            "evidence_recall": self.evidence_recall,
            "latency_p95_ms": self.latency_p95_ms,
            "n": self.n,
        }


def score_run(path: Path, *, system: str, cases_path: Path | None = None) -> SplitScore:
    cases_by_id: dict[str, Any] = {}
    if cases_path and cases_path.exists():
        from agentic_graphrag.eval.metrics import load_cases

        cases_by_id = load_cases(cases_path)
    m = score_system_rows(load_jsonl(path), system=system, cases_by_id=cases_by_id)
    return SplitScore(
        system=system,
        accuracy=m.accuracy,
        evidence_recall=m.evidence_recall,
        latency_p95_ms=m.latency_p95_ms,
        n=m.total,
    )


def build_heldout_report(
    *,
    agentic: SplitScore,
    baseline: SplitScore | None = None,
) -> dict[str, Any]:
    delta_pp = None
    if baseline is not None:
        delta_pp = round((agentic.accuracy - baseline.accuracy) * 100, 2)
    recall_ok = (
        agentic.evidence_recall is not None and agentic.evidence_recall >= RECALL_TARGET
    )
    acc_ok = delta_pp is not None and delta_pp >= ACCURACY_PP_TARGET
    return {
        "schema_version": SCHEMA_VERSION,
        "task": TASK_HELD_OUT,
        "mode": "offline_no_llm",
        "agentic": agentic.to_dict(),
        "baseline": baseline.to_dict() if baseline else None,
        "delta_accuracy_pp": delta_pp,
        "targets": {
            "accuracy_pp_vs_baseline": ACCURACY_PP_TARGET,
            "evidence_recall": RECALL_TARGET,
        },
        "gates": {
            "accuracy_pp_met": acc_ok,
            "evidence_recall_met": recall_ok,
            "formal_g3_claim": False,
            "note": "Offline synthetic heldout — not live AC-1/2 closeout",
        },
    }


def build_triage_report(
    *,
    triage_on: SplitScore,
    force_agentic: SplitScore,
    route_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    loss_pp = round((force_agentic.accuracy - triage_on.accuracy) * 100, 2)
    return {
        "schema_version": SCHEMA_VERSION,
        "task": TASK_TRIAGE,
        "mode": "offline_no_llm",
        "triage_on": triage_on.to_dict(),
        "force_agentic": force_agentic.to_dict(),
        "accuracy_loss_pp": loss_pp,
        "route_counts": route_counts or {},
        "targets": {"max_accuracy_loss_pp": TRIAGE_LOSS_MAX_PP},
        "gates": {
            "loss_within_budget": loss_pp <= TRIAGE_LOSS_MAX_PP,
            "formal_g3_claim": False,
            "note": "Offline Fast Path vs Agentic comparison on same split",
        },
    }


def route_histogram(run_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in load_jsonl(run_path):
        route = str(row.get("route") or "unknown")
        counts[route] = counts.get(route, 0) + 1
    return counts


@dataclass
class IncrementalDrillResult:
    batch_accepted: int = 0
    conflicts_auto: int = 0
    conflicts_review: int = 0
    post_query_ok: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": TASK_INCREMENTAL,
            "batch_accepted": self.batch_accepted,
            "conflicts_auto": self.conflicts_auto,
            "conflicts_review": self.conflicts_review,
            "post_query_ok": self.post_query_ok,
            "details": self.details,
            "gates": {
                "ac5_offline_smoke": self.post_query_ok and self.batch_accepted >= 0,
                "formal_g3_claim": False,
            },
        }


@dataclass(frozen=True)
class _EdgeSpec:
    head: str
    head_type: str
    rel: str
    tail: str
    tail_type: str
    conf: float


def run_incremental_drill() -> IncrementalDrillResult:
    """AC-5 offline smoke: apply batch without clear, then query graph."""
    store = InMemoryGraphStore()
    seed = [
        _EdgeSpec("Acme", "Company", "SUBSIDIARY_OF", "HoldCo", "Company", 0.9),
        _EdgeSpec("Elena", "Person", "CEO_OF", "HoldCo", "Company", 0.95),
    ]
    from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph

    load_triples_into_graph(store, [_to_triple(s) for s in seed], clear_first=True)
    updater = IncrementalUpdater(store, confidence_threshold=0.5)
    batch = [
        _EdgeSpec("Acme", "Company", "SUBSIDIARY_OF", "HoldCo", "Company", 0.99),
        _EdgeSpec("Nova", "Company", "SUBSIDIARY_OF", "HoldCo", "Company", 0.9),
        _EdgeSpec("Elena", "Person", "CEO_OF", "OtherCo", "Company", 0.6),
    ]
    result = updater.apply_batch([_to_triple(s) for s in batch])
    neigh = store.neighbors("HoldCo", max_hops=1, limit=20)
    return IncrementalDrillResult(
        batch_accepted=result.accepted,
        conflicts_auto=result.conflicts_auto,
        conflicts_review=result.conflicts_review,
        post_query_ok=len(neigh) >= 1,
        details={
            "batch_id": result.batch_id,
            "neighbor_hits": len(neigh),
            "store_counts": store.counts(),
        },
    )


def _to_triple(spec: _EdgeSpec) -> Triple:
    return Triple(
        head=EntityMention(name=spec.head, type=spec.head_type),
        relation=spec.rel,
        tail=EntityMention(name=spec.tail, type=spec.tail_type),
        confidence=spec.conf,
    )


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def assemble_g3_scaffold(
    *,
    heldout: dict[str, Any] | None,
    triage: dict[str, Any] | None,
    incremental: dict[str, Any] | None,
    perf_path: Path | None = None,
) -> dict[str, Any]:
    """P3-EV-03 scaffold — offline evidence pack, not formal G3 sign-off."""
    return {
        "schema_version": SCHEMA_VERSION,
        "task": "P3-EV-03",
        "status": "scaffold_offline",
        "formal_g3_go": False,
        "sections": {
            "heldout_effect": heldout,
            "triage_ablation": triage,
            "incremental_drill": incremental,
            "perf_guardrails_path": str(perf_path) if perf_path else None,
        },
        "open_items": [
            "live LLM heldout for AC-1/2",
            "production P95 for AC-4",
            "product domain lock for G2/G3 effect claims",
        ],
    }
