"""CI gate: fail when an offline eval run drops below its accuracy floor.

Reads the ``poc_accuracy.json`` written by ``agr-run-cases`` and compares the
number of correct cases with a fixed floor. Floors live in
``.github/workflows/ci.yml`` (``POC_MIN_CORRECT`` / ``G2_MIN_CORRECT``, currently
30/31 and 196/200); raise them when accuracy improves.

Usage:
    python scripts/check_eval_floor.py reports/poc_accuracy.json --min-correct 30 --total 31
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("accuracy_json", type=Path)
    parser.add_argument("--min-correct", type=int, required=True)
    parser.add_argument("--total", type=int, default=None, help="Expected case count.")
    parser.add_argument("--label", default="offline eval")
    return parser.parse_args(argv)


def check(summary: dict, *, min_correct: int, total: int | None) -> list[str]:
    """Return the list of gate violations (empty when the run passes)."""
    errors: list[str] = []
    correct = int(summary.get("correct", 0))
    seen = int(summary.get("total", 0))
    if total is not None and seen != total:
        errors.append(f"expected {total} cases, got {seen}")
    if correct < min_correct:
        errors.append(f"correct {correct} < floor {min_correct}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    summary = json.loads(args.accuracy_json.read_text(encoding="utf-8"))
    errors = check(summary, min_correct=args.min_correct, total=args.total)
    line = f"{args.label}: {summary.get('correct')}/{summary.get('total')}"
    if errors:
        print(f"FAIL {line} — " + "; ".join(errors))
        return 1
    print(f"OK {line} (floor {args.min_correct})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
