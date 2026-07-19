"""python -m agentic_graphrag <command>"""

from __future__ import annotations

import sys

from agentic_graphrag.cli import (
    build_graph_main,
    eval_main,
    export_reasoning_schema_main,
    index_main,
    ingest_main,
    query_main,
    run_baseline_main,
    run_cases_main,
    score_main,
    score_spotcheck_main,
    spotcheck_main,
)

_COMMANDS = {
    "ingest": ingest_main,
    "build-graph": build_graph_main,
    "index": index_main,
    "run-cases": run_cases_main,
    "run-baseline": run_baseline_main,
    "eval": eval_main,
    "query": query_main,
    "score": score_main,
    "spotcheck": spotcheck_main,
    "score-spotcheck": score_spotcheck_main,
    "export-reasoning-schema": export_reasoning_schema_main,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print(
            "Usage: python -m agentic_graphrag "
            "<ingest|build-graph|index|run-cases|run-baseline|eval|query|score|"
            "spotcheck|score-spotcheck|export-reasoning-schema> [args...]"
        )
        sys.exit(0 if len(sys.argv) > 1 else 1)
    cmd = sys.argv[1]
    if cmd not in _COMMANDS:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)
    _COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
