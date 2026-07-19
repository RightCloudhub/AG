"""python -m agentic_graphrag <command>"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(
            "Usage: python -m agentic_graphrag <command> ...\n"
            "Commands: ingest, build-graph, index, run-cases, run-baseline, score, eval,\n"
            "          gen-cases, pilot-triples, badcase, spotcheck, score-spotcheck,\n"
            "          export-reasoning-schema, query"
        )
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "ingest":
        from agentic_graphrag.cli import ingest_main

        ingest_main(rest)
    elif cmd == "build-graph":
        from agentic_graphrag.cli import build_graph_main

        build_graph_main(rest)
    elif cmd == "index":
        from agentic_graphrag.cli import index_main

        index_main(rest)
    elif cmd == "run-cases":
        from agentic_graphrag.cli import run_cases_main

        run_cases_main(rest)
    elif cmd == "run-baseline":
        from agentic_graphrag.cli import run_baseline_main

        run_baseline_main(rest)
    elif cmd == "score":
        from agentic_graphrag.cli import score_main

        score_main(rest)
    elif cmd == "eval":
        from agentic_graphrag.cli import eval_main

        eval_main(rest)
    elif cmd == "gen-cases":
        from agentic_graphrag.cli import gen_cases_main

        gen_cases_main(rest)
    elif cmd == "pilot-triples":
        from agentic_graphrag.cli import pilot_triples_main

        pilot_triples_main(rest)
    elif cmd == "badcase":
        from agentic_graphrag.cli import badcase_main

        badcase_main(rest)
    elif cmd == "spotcheck":
        from agentic_graphrag.cli import spotcheck_main

        spotcheck_main(rest)
    elif cmd == "score-spotcheck":
        from agentic_graphrag.cli import score_spotcheck_main

        score_spotcheck_main(rest)
    elif cmd == "export-reasoning-schema":
        from agentic_graphrag.cli import export_reasoning_schema_main

        export_reasoning_schema_main(rest)
    elif cmd == "query":
        from agentic_graphrag.cli import query_main

        query_main(rest)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
