#!/usr/bin/env python3
"""Sample audit chains for production AC-3 / P4-AC-02 checklist.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/sample_audit.py
  PYTHONPATH=src .venv/bin/python scripts/sample_audit.py --n 20 --out reports/audit_sample.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="Stratified audit chain sampling")
    ap.add_argument(
        "--audit",
        default=str(ROOT / "data/processed/audit_chains.jsonl"),
        help="Audit JSONL path",
    )
    ap.add_argument("--n", type=int, default=20, help="Target sample size")
    ap.add_argument(
        "--out",
        default="",
        help="Output JSONL (default reports/audit_sample_YYYYMMDD.jsonl)",
    )
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    path = Path(args.audit)
    if not path.exists():
        print(f"No audit file: {path}")
        return 1

    rows = _load(path)
    if not rows:
        print("Audit file empty")
        return 1

    sample = _stratified(rows, n=args.n, seed=args.seed)
    out = Path(args.out) if args.out else ROOT / "reports" / f"audit_sample_{date.today().isoformat()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in sample:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    checklist = {
        "sample_size": len(sample),
        "source": str(path),
        "criteria": [
            "query_id present",
            "steps non-empty for agentic answered",
            "citations present when status=answered",
            "GET /v1/audit/queries/{id} resolves for each sample (same tenant)",
        ],
        "ids": [r.get("query_id") for r in sample],
    }
    meta = out.with_suffix(".checklist.json")
    meta.write_text(json.dumps(checklist, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Sampled {len(sample)} → {out}")
    print(f"Checklist → {meta}")
    return 0


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _stratified(rows: list[dict], *, n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        route = str(r.get("route") or (r.get("metadata") or {}).get("route") or "unknown")
        status = str(r.get("status") or "unknown")
        buckets[f"{route}:{status}"].append(r)
    # Round-robin from buckets for diversity
    for b in buckets.values():
        rng.shuffle(b)
    keys = list(buckets.keys())
    rng.shuffle(keys)
    out: list[dict] = []
    idx = {k: 0 for k in keys}
    while len(out) < min(n, len(rows)):
        progressed = False
        for k in keys:
            i = idx[k]
            if i < len(buckets[k]):
                out.append(buckets[k][i])
                idx[k] = i + 1
                progressed = True
                if len(out) >= n:
                    break
        if not progressed:
            break
    return out


if __name__ == "__main__":
    raise SystemExit(main())
