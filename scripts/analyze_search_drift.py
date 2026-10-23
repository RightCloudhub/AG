"""Report search drift from same-stack runs; LLM labels are optional input data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentic_graphrag.eval.metrics import load_cases, load_jsonl
from agentic_graphrag.eval.search_drift import gold_chunks, reachable_unasked, summarize


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = load_jsonl(args.run)
    if not rows:
        parser.error("run is missing or empty")
    cases = load_cases(args.cases)
    report = summarize(rows, cases)
    if args.base:
        base = {str(r.get("case_id") or r.get("id")): r for r in load_jsonl(args.base)}
        candidate = {str(r.get("case_id") or r.get("id")): r for r in rows}
        if base.keys() != candidate.keys():
            parser.error("baseline and candidate case ids must match")
        report["reachable_unasked"] = {
            cid: reachable_unasked(base[cid], row, gold_chunks(cases.get(cid, {}))[0])
            for cid, row in candidate.items()
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"cases", "reachable_unasked"}},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
