#!/usr/bin/env python3
"""HTTP load harness for AC-4 / G3 P95 (P3-PERF-07).

Offline in-process mode (default) and HTTP mode (--target).

Usage:
  PYTHONPATH=src .venv/bin/python scripts/p3_load_http.py
  PYTHONPATH=src .venv/bin/python scripts/p3_load_http.py --target http://127.0.0.1:8000 --n 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

CASES = [
    {"id": "simple", "q": "Who is the CEO of Apex Holdings?", "force_agentic": False},
    {
        "id": "multihop",
        "q": "Who is the CEO of the parent company of BrightLink Logistics?",
        "force_agentic": True,
    },
    {
        "id": "guard_long",
        "q": ("What " * 40) + "is the CEO of Apex Holdings?",
        "force_agentic": False,
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="", help="HTTP base URL; empty = in-process offline")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--api-key", default="")
    ap.add_argument("--out", default="reports/g3_offline/load_smoke.json")
    ap.add_argument("--no-cache-bust", action="store_true")
    args = ap.parse_args()

    results: list[dict[str, Any]] = []
    if args.target:
        results = _http_load(args)
        mode = "http"
    else:
        results = _offline_load(args)
        mode = "offline_inprocess"

    by_route: dict[str, list[int]] = {}
    errors = 0
    for r in results:
        by_route.setdefault(r.get("route") or "unknown", []).append(int(r["latency_ms"]))
        if r.get("error"):
            errors += 1

    report = {
        "mode": mode,
        "n": len(results),
        "concurrency": args.concurrency,
        "error_rate": errors / len(results) if results else 0.0,
        "latency_p50_ms": _pct([r["latency_ms"] for r in results], 50),
        "latency_p95_ms": _pct([r["latency_ms"] for r in results], 95),
        "latency_p99_ms": _pct([r["latency_ms"] for r in results], 99),
        "latency_by_route": {
            k: {
                "p50_ms": _pct(v, 50),
                "p95_ms": _pct(v, 95),
                "count": len(v),
            }
            for k, v in by_route.items()
        },
        "targets": {
            "agentic_p95_ms": 8000,
            "fast_path_p95_ms": 3000,
            "note": "Live staging required for formal AC-4; offline is smoke only",
        },
        "samples": results[:20],
    }
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("mode", "n", "latency_p95_ms", "error_rate")}, indent=2))
    print(f"→ {out}")
    return 0


def _offline_load(args: argparse.Namespace) -> list[dict[str, Any]]:
    from agentic_graphrag.api.schemas import QueryRequest
    from agentic_graphrag.api.service import QueryService

    svc = QueryService.create_offline()
    # Disable answer cache so load p95 is not dominated by hits.
    svc.enable_cache = False
    results: list[dict[str, Any]] = []
    for i in range(args.n):
        case = CASES[i % len(CASES)]
        q = case["q"]
        if not args.no_cache_bust:
            q = f"{q} [load:{i}]"
        t0 = time.perf_counter()
        try:
            data = svc.run_query(
                QueryRequest(question=q[:2000], force_agentic=case["force_agentic"])
            )
            ms = int((time.perf_counter() - t0) * 1000)
            results.append(
                {
                    "id": case["id"],
                    "route": data.route,
                    "status": data.status,
                    "latency_ms": ms,
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            ms = int((time.perf_counter() - t0) * 1000)
            results.append(
                {
                    "id": case["id"],
                    "route": "error",
                    "status": "error",
                    "latency_ms": ms,
                    "error": type(exc).__name__,
                }
            )
    return results


def _http_load(args: argparse.Namespace) -> list[dict[str, Any]]:
    import urllib.error
    import urllib.request

    base = args.target.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    def one(i: int) -> dict[str, Any]:
        case = CASES[i % len(CASES)]
        q = case["q"]
        if not args.no_cache_bust:
            q = f"{q} [{uuid.uuid4().hex[:8]}]"
        body = json.dumps(
            {"question": q[:2000], "force_agentic": case["force_agentic"]}
        ).encode()
        req = urllib.request.Request(
            f"{base}/v1/query", data=body, headers=headers, method="POST"
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode())
            ms = int((time.perf_counter() - t0) * 1000)
            data = payload.get("data") or payload
            return {
                "id": case["id"],
                "route": data.get("route") or "unknown",
                "status": data.get("status") or "",
                "latency_ms": ms,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            ms = int((time.perf_counter() - t0) * 1000)
            return {
                "id": case["id"],
                "route": "error",
                "status": "error",
                "latency_ms": ms,
                "error": type(exc).__name__,
            }

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futs = [pool.submit(one, i) for i in range(args.n)]
        for fut in as_completed(futs):
            results.append(fut.result())
    return results


def _pct(xs: list[int], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    idx = int(round((p / 100.0) * (len(s) - 1)))
    return float(s[idx])


if __name__ == "__main__":
    raise SystemExit(main())
