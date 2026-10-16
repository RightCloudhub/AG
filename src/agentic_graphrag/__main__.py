"""python -m agentic_graphrag <command>"""

from __future__ import annotations

import sys
from collections.abc import Callable

_COMMANDS: dict[str, str] = {
    "ingest": "ingest_main",
    "build-graph": "build_graph_main",
    "index": "index_main",
    "run-cases": "run_cases_main",
    "run-baseline": "run_baseline_main",
    "score": "score_main",
    "eval": "eval_main",
    "gen-cases": "gen_cases_main",
    "pilot-triples": "pilot_triples_main",
    "badcase": "badcase_main",
    "spotcheck": "spotcheck_main",
    "score-spotcheck": "score_spotcheck_main",
    "export-reasoning-schema": "export_reasoning_schema_main",
    "query": "query_main",
}


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        _print_help()
        return
    cmd, rest = argv[0], argv[1:]
    handler = _resolve(cmd)
    if handler is None:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)
    handler(rest)


def _print_help() -> None:
    print(
        "Usage: python -m agentic_graphrag <command> ...\n"
        "Commands: ingest, build-graph, index, run-cases, run-baseline, score, eval,\n"
        "          gen-cases, pilot-triples, badcase, spotcheck, score-spotcheck,\n"
        "          export-reasoning-schema, query"
    )


def _resolve(cmd: str) -> Callable[[list[str]], None] | None:
    name = _COMMANDS.get(cmd)
    if name is None:
        return None
    from agentic_graphrag import cli as cli_pkg

    try:
        handler = getattr(cli_pkg, name)
    except AttributeError:
        return None
    if not callable(handler):
        return None
    return handler


if __name__ == "__main__":
    main()
