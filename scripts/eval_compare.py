"""Paired A/B comparison of two ``agr-run-cases`` reports.

Usage:
    python scripts/eval_compare.py BASE.jsonl CANDIDATE.jsonl \
        --cases evals/datasets/g2_all.jsonl [--json out.json] [--fail-on-worse]

Exit code 1 with ``--fail-on-worse`` when the candidate is significantly worse
(exact McNemar, p < 0.05).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_graphrag.eval.compare import compare_runs, format_text, load_cases, load_rows


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None, help="Write the full report here.")
    parser.add_argument("--fail-on-worse", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    report = compare_runs(load_rows(args.base), load_rows(args.candidate), load_cases(args.cases))
    print(format_text(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if args.fail_on_worse and report["verdict"] == "worse" else 0


if __name__ == "__main__":
    sys.exit(main())
