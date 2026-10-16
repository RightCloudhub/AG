"""Eval-set generation, split, and badcase attribution CLIs (P2-EV-02/05)."""

from __future__ import annotations

import argparse
import json
import sys

from agentic_graphrag.config import get_config, resolve_path
from agentic_graphrag.eval.cases import (
    StratificationSpec,
    dump_cases,
    validate_stratification,
)
from agentic_graphrag.eval.gold_gen import (
    generate_guardrail_set,
    generate_stratified_eval_set,
)
from agentic_graphrag.eval.split_sets import split_gold_cases, split_summary, write_split_datasets
from agentic_graphrag.knowledge.pilot_triples import write_pilot_triples
from agentic_graphrag.knowledge.schema_check import Triple


def gen_cases_main(argv: list[str] | None = None) -> None:
    """P2-EV-02: build ≥200 stratified gold cases + splits from pilot/seed triples."""
    args = _parse_gen_cases(argv)
    triples = _load_or_write_triples(args)
    spec = StratificationSpec(
        min_total=args.min_total,
        min_2hop=args.min_2hop,
        min_3hop=args.min_3hop,
        min_open=args.min_open,
        min_no_answer=args.min_no_answer,
    )
    cases = generate_stratified_eval_set(triples, spec)
    report = validate_stratification(cases, spec, strict_total=True)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    if not report.ok:
        print("ERROR: stratification targets not met", file=sys.stderr)
        dump_cases(cases, resolve_path(args.out_dir) / "g2_partial.jsonl")
        sys.exit(1)
    _write_dataset(args, cases=cases, triples=triples, report=report)


def _parse_gen_cases(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate stratified G2 eval cases (P2-EV-02)")
    parser.add_argument("--triples", default=None)
    parser.add_argument("--write-pilot-triples", default="data/processed/pilot_triples.jsonl")
    parser.add_argument("--out-dir", default="evals/datasets")
    parser.add_argument("--min-total", type=int, default=200)
    parser.add_argument("--min-2hop", type=int, default=90)
    parser.add_argument("--min-3hop", type=int, default=60)
    parser.add_argument("--min-open", type=int, default=30)
    parser.add_argument("--min-no-answer", type=int, default=20)
    parser.add_argument("--heldout-ratio", type=float, default=0.25)
    parser.add_argument("--guardrail-n", type=int, default=25)
    return parser.parse_args(argv)


def _load_or_write_triples(args: argparse.Namespace) -> list[Triple]:
    if args.triples:
        path = resolve_path(args.triples)
        triples = [
            Triple.model_validate(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(f"Loaded {len(triples)} triples from {path}")
        return triples
    out_t = resolve_path(args.write_pilot_triples)
    triples = write_pilot_triples(out_t)
    print(f"Wrote {len(triples)} pilot triples → {out_t}")
    return triples


def _write_dataset(args, *, cases, triples, report) -> None:
    guardrail = generate_guardrail_set(max_n=args.guardrail_n)
    out_dir = resolve_path(args.out_dir)
    paths = write_split_datasets(
        cases,
        out_dir,
        heldout_ratio=args.heldout_ratio,
        guardrail=guardrail,
        stem="g2",
    )
    splits = split_gold_cases(cases, heldout_ratio=args.heldout_ratio, guardrail=guardrail)
    summary = {
        "task": "P2-EV-02",
        "triples": len(triples),
        "gold_total": len(cases),
        "stratification": report.to_dict(),
        "splits": split_summary(splits),
        "paths": {k: str(v) for k, v in paths.items()},
    }
    summary_path = out_dir / "g2_dataset_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary["splits"], ensure_ascii=False, indent=2))
    print(f"Summary → {summary_path}")
    for k in ("all", "dev", "heldout", "guardrail"):
        print(f"  {k}: {paths[k]}")


def badcase_main(argv: list[str] | None = None) -> None:
    """P2-EV-05: attribute incorrect run rows into four buckets."""
    parser = argparse.ArgumentParser(description="Badcase attribution (P2-EV-05)")
    parser.add_argument("--run", required=True, help="Agentic run JSONL")
    parser.add_argument("--cases", default=None, help="Gold cases JSONL")
    parser.add_argument("--out", default="reports/badcase_attribution.json")
    args = parser.parse_args(argv)
    cfg = get_config()
    from agentic_graphrag.eval.badcase import attribute_run
    from agentic_graphrag.eval.metrics import load_cases as load_case_dict
    from agentic_graphrag.eval.metrics import load_jsonl

    run_path = resolve_path(args.run)
    cases_path = resolve_path(args.cases or cfg.eval.cases_path)
    rows = load_jsonl(run_path)
    cases_by_id = load_case_dict(cases_path) if cases_path.exists() else {}
    report = attribute_run(rows, cases_by_id)
    out = resolve_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["by_attribution"], ensure_ascii=False, indent=2))
    print(f"Badcases: {report['bad']} / {report['total']} → {out}")


def pilot_triples_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write deterministic pilot triples (P2-KG-04)")
    parser.add_argument("--out", default="data/processed/pilot_triples.jsonl")
    args = parser.parse_args(argv)
    triples = write_pilot_triples(resolve_path(args.out))
    print(f"Wrote {len(triples)} triples → {resolve_path(args.out)}")
