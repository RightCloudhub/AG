#!/usr/bin/env python3
"""P3-OP-04 / P3-PERF-07: guardrail suite + lightweight latency smoke.

Usage:
  python scripts/p3_guardrail_and_load.py
  python scripts/p3_guardrail_and_load.py --out reports/p3_perf_guardrails.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_query
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.config import resolve_path


GUARDRAIL_CASES = [
    {"id": "g_diverge", "q": "Tell me everything about everything in the universe forever"},
    {"id": "g_loop", "q": "What is the parent of the parent of the parent of the parent of Apex?"},
    {"id": "g_long", "q": ("What " * 200) + "is the CEO of Apex Holdings?"},
    {"id": "g_simple", "q": "Who is the CEO of Apex Holdings?"},
    {"id": "g_multihop", "q": "Who is the CEO of the parent company of BrightLink Logistics?"},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/p3_perf_guardrails.json")
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()

    svc = QueryService.create_offline()
    latencies: list[int] = []
    routes: dict[str, int] = {}
    results = []

    try:
        for case in GUARDRAIL_CASES:
            t0 = time.perf_counter()
            from agentic_graphrag.api.schemas import QueryRequest

            data = svc.run_query(QueryRequest(question=case["q"][:2000]))
            ms = int((time.perf_counter() - t0) * 1000)
            latencies.append(ms)
            routes[data.route] = routes.get(data.route, 0) + 1
            results.append(
                {
                    "id": case["id"],
                    "route": data.route,
                    "status": data.status,
                    "latency_ms": ms,
                    "hops": len(data.steps),
                    "answer_preview": (data.answer or "")[:120],
                }
            )

        # Load smoke: repeat simple/multihop
        load_lat: list[int] = []
        for _ in range(args.repeat):
            t0 = time.perf_counter()
            from agentic_graphrag.api.schemas import QueryRequest

            svc.run_query(
                QueryRequest(question="Who is the CEO of Apex Holdings?", force_agentic=False)
            )
            load_lat.append(int((time.perf_counter() - t0) * 1000))

        def p95(xs: list[int]) -> float:
            if not xs:
                return 0.0
            s = sorted(xs)
            return float(s[int(0.95 * (len(s) - 1))])

        report = {
            "guardrail_cases": results,
            "route_counts": routes,
            "latency_ms": {
                "mean": statistics.mean(latencies) if latencies else 0,
                "p95": p95(latencies),
                "max": max(latencies) if latencies else 0,
            },
            "load_repeat": {
                "n": len(load_lat),
                "mean_ms": statistics.mean(load_lat) if load_lat else 0,
                "p95_ms": p95(load_lat),
            },
            "note": "Offline path; live LLM P95 targets apply under production load.",
        }
        out = resolve_path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report["latency_ms"], indent=2))
        print(f"wrote {out}")
        return 0
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
