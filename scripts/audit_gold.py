"""Audit gold labels of an eval dataset against seed triples (report only).

Usage:
    python scripts/audit_gold.py evals/datasets/g2_all.jsonl \
        --triples data/processed/pilot_triples.jsonl [--json reports/gold_audit.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_graphrag.eval.gold_audit import audit_cases, load_triples


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", type=Path)
    parser.add_argument("--triples", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--max-flagged",
        type=int,
        default=None,
        help="exit 1 when more cases are flagged (default: report only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    lines = args.cases.read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    report = audit_cases(cases, load_triples(args.triples))
    print(f"cases={report['total']} flagged={report['flagged']} by_issue={report['by_issue']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.max_flagged is not None and report["flagged"] > args.max_flagged:
        print(f"FAIL: {report['flagged']} flagged > {args.max_flagged}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
