"""Single ad-hoc query command."""

from __future__ import annotations

import argparse
import json

from agentic_graphrag.cli.cases import run_cases_main
from agentic_graphrag.config import resolve_path


def query_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Single query (agentic)")
    parser.add_argument("question")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument(
        "--memory-graph", action="store_true", help="Use in-memory graph from seed triples"
    )
    parser.add_argument("--neo4j", action="store_true", help="Force Neo4j graph backend")
    parser.add_argument("--seed-triples", default="data/processed/seed_triples.jsonl")
    args = parser.parse_args(argv)
    # Reuse run_cases machinery for one question
    cases_path = resolve_path("data/processed/_single_case.jsonl")
    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases_path.write_text(
        json.dumps(
            {"id": "adhoc", "question": args.question, "gold_answer": ""}, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    run_args = ["--cases", str(cases_path), "--seed-triples", args.seed_triples]
    if args.no_llm:
        run_args.append("--no-llm")
    if args.memory_graph:
        run_args.append("--memory-graph")
    if args.neo4j:
        run_args.append("--neo4j")
    run_cases_main(run_args)
