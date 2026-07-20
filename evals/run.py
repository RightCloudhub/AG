"""One-click eval entry (plan/engineering/repo-structure.md · FR-OP-04).

Thin facade over ``agentic_graphrag`` CLI / eval modules so the tree matches
the documented layout. Prefer console scripts in production CI.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    """Delegate to ``agr-eval`` comparison report builder."""
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from agentic_graphrag.cli.cases import eval_main

    eval_main(argv)


if __name__ == "__main__":
    main()
