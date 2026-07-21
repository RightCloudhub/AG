#!/usr/bin/env python3
"""Stratified gold-case sample for ANNOTATION_SPEC §4.2 human review.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/sample_gold_for_review.py
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cases",
        default=str(ROOT / "evals/datasets/g2_all.jsonl"),
    )
    ap.add_argument(
        "--out",
        default=str(ROOT / "evals/datasets/review_queue_gold.jsonl"),
    )
    ap.add_argument("--fraction", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--mins",
        default="2hop:20,3hop:15,open:10,no_answer:0",
        help="Min samples per category (0 = all for that cat if present)",
    )
    args = ap.parse_args()

    mins = _parse_mins(args.mins)
    rows = _load(Path(args.cases))
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        cat = str(r.get("category") or "unknown")
        by_cat[cat].append(r)

    rng = random.Random(args.seed)
    sample: list[dict] = []
    for cat, items in by_cat.items():
        rng.shuffle(items)
        n_frac = max(1, int(len(items) * args.fraction))
        floor = mins.get(cat, 0)
        if floor == 0 and cat == "no_answer":
            take = len(items)  # glance all no_answer
        else:
            take = max(n_frac, floor)
        take = min(take, len(items))
        for r in items[:take]:
            meta = dict(r.get("metadata") or {})
            meta["review_sample"] = True
            meta["annotation_status"] = meta.get("annotation_status") or "pending_review"
            r = {**r, "metadata": meta}
            sample.append(r)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in sample:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Sampled {len(sample)} / {len(rows)} → {out}")
    return 0


def _parse_mins(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip()] = int(v.strip())
    return out


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
