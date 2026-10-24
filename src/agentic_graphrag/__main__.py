"""python -m agentic_graphrag <command>"""

from __future__ import annotations

import sys
from collections.abc import Callable
from enum import StrEnum


class Command(StrEnum):
    INGEST = "ingest"
    BUILD_GRAPH = "build-graph"
    INDEX = "index"
    RUN_CASES = "run-cases"
    RUN_BASELINE = "run-baseline"
    SCORE = "score"
    EVAL = "eval"
    GEN_CASES = "gen-cases"
    PILOT_TRIPLES = "pilot-triples"
    BADCASE = "badcase"
    SPOTCHECK = "spotcheck"
    SCORE_SPOTCHECK = "score-spotcheck"
    EXPORT_REASONING_SCHEMA = "export-reasoning-schema"
    QUERY = "query"


def _commands() -> dict[Command, Callable[[list[str]], None]]:
    from agentic_graphrag import cli

    return {
        Command.INGEST: cli.ingest_main,
        Command.BUILD_GRAPH: cli.build_graph_main,
        Command.INDEX: cli.index_main,
        Command.RUN_CASES: cli.run_cases_main,
        Command.RUN_BASELINE: cli.run_baseline_main,
        Command.SCORE: cli.score_main,
        Command.EVAL: cli.eval_main,
        Command.GEN_CASES: cli.gen_cases_main,
        Command.PILOT_TRIPLES: cli.pilot_triples_main,
        Command.BADCASE: cli.badcase_main,
        Command.SPOTCHECK: cli.spotcheck_main,
        Command.SCORE_SPOTCHECK: cli.score_spotcheck_main,
        Command.EXPORT_REASONING_SCHEMA: cli.export_reasoning_schema_main,
        Command.QUERY: cli.query_main,
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
    try:
        command = Command(cmd)
    except ValueError:
        return None
    return _commands()[command]


if __name__ == "__main__":
    main()
