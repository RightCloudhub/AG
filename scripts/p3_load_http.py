#!/usr/bin/env python3
"""HTTP / in-process load harness for AC-4 P95 (P3-PERF-07).

Usage:
  PYTHONPATH=src .venv/bin/python scripts/p3_load_http.py --n 20
  PYTHONPATH=src .venv/bin/python scripts/p3_load_http.py \
    --target http://127.0.0.1:8000 --n 30 --concurrency 4
"""

from __future__ import annotations

import argparse
import json
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
        "id": "competitor_chain",
        "q": "Who is the CEO of the competitor of the producer of HelixCore Server?",
        "force_agentic": True,
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="", help="HTTP base; empty = offline in-process")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--api-key", default="")
    ap.add_argument("--out", default="reports/g3_offline/load_p95.json")
    ap.add_argument("--live-llm", action="store_true", help="In-process with allow_llm if key set")
    ap.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-request HTTP timeout seconds (default 60; was 180)",
    )
    ap.add_argument(
        "--warmup",
        action="store_true",
        help="One sequential probe before load; abort if probe fails",
    )
    ap.add_argument(
        "--simple-only",
        action="store_true",
        help="Only simple Fast Path questions (no force_agentic multihop)",
    )
    ap.add_argument(
        "--stable-questions",
        action="store_true",
        help="Do not append unique suffixes (allows answer-cache hits for warm P95)",
    )
    args = ap.parse_args()

    if args.simple_only:
        global CASES
        CASES = [c for c in CASES if not c.get("force_agentic")]

    if args.target and args.warmup:
        probe = _http_one(args, 0, case_override=CASES[0])
        if probe.get("error"):
            print(
                "WARMUP FAILED — fix API/LLM before load. Probe:",
                json.dumps(probe, ensure_ascii=False),
            )
            print(
                "Hints: curl healthz; single POST /v1/query; if AGR_ALLOW_LLM=1, "
                "verify LLM_BASE_URL responds; restart agr-api to clear stuck workers."
            )
            out = Path(args.out)
            if not out.is_absolute():
                out = ROOT / out
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {
                        "mode": "http",
                        "status": "warmup_failed",
                        "probe": probe,
                        "formal_ac4": False,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return 2

    results = _http_load(args) if args.target else _offline_load(args)
    by_route: dict[str, list[int]] = {}
    errors = 0
    err_types: dict[str, int] = {}
    for r in results:
        by_route.setdefault(str(r.get("route") or "unknown"), []).append(int(r["latency_ms"]))
        if r.get("error"):
            errors += 1
            et = str(r.get("error") or "error")
            err_types[et] = err_types.get(et, 0) + 1

    agentic_p95 = _pct(by_route.get("agentic") or [], 95)
    fast_p95 = _pct(by_route.get("fast_path") or [], 95)
    error_rate = errors / len(results) if results else 0.0
    degraded_n = sum(1 for r in results if r.get("llm_degraded"))
    cache_n = sum(1 for r in results if r.get("cache_hit"))
    live_ok_n = sum(
        1
        for r in results
        if not r.get("error")
        and not r.get("llm_degraded")
        and not r.get("cache_hit")
        and (r.get("llm_calls") or 0) > 0
    )
    diagnosis = None
    if error_rate >= 0.9:
        diagnosis = (
            "Almost all requests failed — this is NOT a latency P95 result. "
            f"error_types={err_types}. "
            "Typical: TimeoutError with AGR_ALLOW_LLM=1 when LLM/embed hangs; "
            "or API overloaded by concurrency. "
            "Retry: concurrency=1 --warmup --simple-only --timeout 45; "
            "or offline load without --target."
        )
    elif degraded_n >= max(1, int(0.5 * len(results))):
        diagnosis = (
            f"{degraded_n}/{len(results)} responses have metadata.llm_degraded "
            "(LLM connect/timeout → offline extractive answer). "
            "P95 is dominated by failed SSL/connect wait, NOT live LLM latency. "
            "Fix LLM_BASE_URL connectivity; AC-4 requires live_ok (llm_calls>0, no degrade)."
        )
    report = {
        "mode": "http" if args.target else "offline_inprocess",
        "n": len(results),
        "concurrency": args.concurrency,
        "timeout_s": args.timeout if args.target else None,
        "error_rate": error_rate,
        "error_types": err_types,
        "diagnosis": diagnosis,
        "latency_p50_ms": _pct([r["latency_ms"] for r in results], 50),
        "latency_p95_ms": _pct([r["latency_ms"] for r in results], 95),
        "latency_by_route": {
            k: {"p50_ms": _pct(v, 50), "p95_ms": _pct(v, 95), "count": len(v)}
            for k, v in by_route.items()
        },
        "targets": {
            "agentic_p95_ms": 8000,
            "fast_path_p95_ms": 3000,
            "agentic_p95_met": bool(error_rate < 0.1 and agentic_p95 and agentic_p95 <= 8000),
            "fast_path_p95_met": bool(error_rate < 0.1 and fast_p95 and fast_p95 <= 3000),
            # Cold live path only: cache-only warm runs are reported separately.
            "formal_ac4_cold": bool(
                args.target
                and error_rate < 0.1
                and degraded_n == 0
                and cache_n == 0
                and live_ok_n >= max(1, int(0.8 * len(results)))
                and (
                    (agentic_p95 and agentic_p95 <= 8000)
                    or (fast_p95 and fast_p95 <= 3000 and not (by_route.get("agentic")))
                )
            ),
            "formal_ac4": False,  # set below after warm flag
            "note": "AC-4 cold: live LLM, no degrade, no answer-cache. "
            "Warm cache is ops evidence only (not PRD cold P95).",
        },
        "llm_degraded_count": degraded_n,
        "live_ok_count": live_ok_n,
        "cache_hit_count": cache_n,
        "samples": results[:15],
    }
    # formal_ac4: cold live only; warm cache gets separate flag
    warm_ok = (
        args.target
        and error_rate < 0.1
        and degraded_n == 0
        and cache_n >= max(1, int(0.8 * len(results)))
        and fast_p95 is not None
        and fast_p95 <= 3000
    )
    report["targets"]["formal_ac4"] = bool(report["targets"]["formal_ac4_cold"])
    report["targets"]["warm_cache_ok"] = bool(warm_ok)
    if cache_n >= max(1, int(0.5 * len(results))) and not report["targets"]["formal_ac4_cold"]:
        report["diagnosis"] = (
            report.get("diagnosis")
            or f"{cache_n}/{len(results)} answer-cache hits — warm P95 is valid for hot-path ops, "
            "not for PRD AC-4 cold latency. See cold run (no --stable-questions)."
        )
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {
        "mode": report["mode"],
        "n": report["n"],
        "error_rate": report["error_rate"],
        "error_types": report.get("error_types"),
        "llm_degraded_count": report.get("llm_degraded_count"),
        "live_ok_count": report.get("live_ok_count"),
        "cache_hit_count": report.get("cache_hit_count"),
        "latency_p95_ms": report["latency_p95_ms"],
        "by_route_p95": {k: v["p95_ms"] for k, v in report["latency_by_route"].items()},
        "targets": report["targets"],
        "diagnosis": report.get("diagnosis"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"→ {out}")
    return 1 if error_rate >= 0.9 else 0


def _offline_load(args: argparse.Namespace) -> list[dict[str, Any]]:
    from agentic_graphrag.api.schemas import QueryRequest
    from agentic_graphrag.api.service import QueryService

    svc = QueryService.create_offline()
    svc.enable_cache = False  # avoid cache-zero P95 artifact
    if args.live_llm:
        from agentic_graphrag.config import get_settings

        s = get_settings()
        if s.llm_api_key and "your-key" not in s.llm_api_key:
            svc.allow_llm = True
    results: list[dict[str, Any]] = []
    for i in range(args.n):
        case = CASES[i % len(CASES)]
        if getattr(args, "stable_questions", False):
            q = case["q"]
        else:
            q = f"{case['q']} [load:{i}:{uuid.uuid4().hex[:6]}]"
        t0 = time.perf_counter()
        try:
            data = svc.run_query(
                QueryRequest(question=q[:2000], force_agentic=case["force_agentic"])
            )
            results.append(
                {
                    "id": case["id"],
                    "route": data.route,
                    "status": data.status,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "id": case["id"],
                    "route": "error",
                    "status": "error",
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "error": type(exc).__name__,
                }
            )
    return results


def _http_one(
    args: argparse.Namespace,
    i: int,
    *,
    case_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import urllib.error
    import urllib.request

    base = args.target.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    case = case_override or CASES[i % len(CASES)]
    if getattr(args, "stable_questions", False):
        q = case["q"]
    else:
        q = f"{case['q']} [{uuid.uuid4().hex[:8]}]"
    body = json.dumps(
        {"question": q[:2000], "force_agentic": bool(case.get("force_agentic"))}
    ).encode()
    req = urllib.request.Request(f"{base}/v1/query", data=body, headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=float(args.timeout)) as resp:
            payload = json.loads(resp.read().decode())
        data = payload.get("data") or payload
        meta = data.get("metadata") or {}
        return {
            "id": case["id"],
            "route": data.get("route") or "unknown",
            "status": data.get("status") or "",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "error": None,
            "llm_degraded": bool(meta.get("llm_degraded")),
            "llm_error": meta.get("llm_error"),
            "llm_calls": (data.get("cost") or {}).get("llm_calls"),
            "cache_hit": bool(meta.get("cache_hit")),
        }
    except Exception as exc:  # noqa: BLE001
        detail = type(exc).__name__
        if isinstance(exc, urllib.error.HTTPError):
            detail = f"HTTPError_{exc.code}"
        return {
            "id": case["id"],
            "route": "error",
            "status": "error",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "error": detail,
        }


def _http_load(args: argparse.Namespace) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futs = [pool.submit(_http_one, args, i) for i in range(args.n)]
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
