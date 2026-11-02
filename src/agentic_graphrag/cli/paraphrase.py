"""Offline-default paraphrase evaluation through the same QueryApp as run-cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from agentic_graphrag.cli.cases_run import (
    build_eval_app,
    known_entity_names,
    load_seed_triples,
    run_case_row_via_app,
)
from agentic_graphrag.config import resolve_path
from agentic_graphrag.eval.cases import load_cases
from agentic_graphrag.eval.paraphrase import VARIANTS, group_cases, summarize


def paraphrase_main(argv: list[str] | None = None) -> None:
    args = _arguments(argv)
    dataset, triples = resolve_path(args.cases), resolve_path(args.seed_triples)
    groups = group_cases(load_cases(dataset))
    selected = list(groups.values())[: args.limit_groups]
    cases = [forms[tag] for forms in selected for tag in VARIANTS]
    out = resolve_path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    app = build_eval_app(
        no_llm=not args.live, use_neo4j=False, seed_triples=triples, enable_triage=True
    )
    rows = []
    try:
        if args.live and not app.allow_llm:
            raise ValueError("--live requires a configured non-placeholder LLM_API_KEY")
        with (out / f"{args.run_name}.jsonl").open("w", encoding="utf-8") as stream:
            for case in cases:
                row = run_case_row_via_app(case.model_dump(), app=app)
                # Keep reviewable answers/routing/costs without duplicating the full audit chain.
                row.pop("chain", None)
                row.pop("metadata", None)
                row.pop("explored_paths", None)
                rows.append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
    finally:
        app.close()
    summary = summarize(cases, rows, known_entities=known_entity_names(load_seed_triples(triples)))
    summary.update(
        mode="live" if args.live else "offline",
        triage=True,
        dataset_sha256=_sha256(dataset),
        triples_sha256=_sha256(triples),
    )
    path = out / f"{args.run_name}_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="evals/datasets/paraphrase.jsonl")
    parser.add_argument("--seed-triples", default="data/processed/pilot_triples.jsonl")
    parser.add_argument("--out", default="reports")
    parser.add_argument("--run-name", default="paraphrase_offline")
    parser.add_argument("--limit-groups", type=int, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-llm", action="store_true", help="Default: deterministic offline run")
    mode.add_argument("--live", action="store_true", help="Opt in to the configured LLM")
    args = parser.parse_args(argv)
    if args.limit_groups is not None and args.limit_groups < 1:
        parser.error("--limit-groups must be positive")
    if Path(args.run_name).name != args.run_name:
        parser.error("--run-name must be a filename stem")
    return args
