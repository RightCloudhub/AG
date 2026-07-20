"""Run eval cases, score, and comparison report commands."""

from __future__ import annotations

import argparse
import json
import sys

from agentic_graphrag.cli.cases_run import (
    build_retrievers,
    known_entity_names,
    load_seed_triples,
    open_case_graph,
    run_case_row,
)
from agentic_graphrag.config import get_config, get_settings, resolve_path


def run_cases_main(argv: list[str] | None = None) -> None:
    args = _parse_run_cases(argv)
    if args.memory_graph and args.neo4j:
        print("Conflicting flags: --memory-graph and --neo4j", file=sys.stderr)
        sys.exit(2)
    cfg = get_config()
    settings = get_settings()
    cases_path = resolve_path(args.cases or cfg.eval.cases_path)
    report_dir = resolve_path(args.out or cfg.eval.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    from agentic_graphrag.agent.executor import Executor
    from agentic_graphrag.agent.guardrails import GuardrailConfig
    from agentic_graphrag.eval.scoring import write_accuracy_summary

    triples = load_seed_triples(resolve_path(args.seed_triples))
    known_entities = known_entity_names(triples)
    use_memory = args.memory_graph or (args.no_llm and not args.neo4j)
    graph_store, _backend = open_case_graph(settings, use_memory=use_memory, triples=triples)
    graph_ret, fulltext_ret, vector_ret, llm = build_retrievers(
        cfg, settings, graph_store, no_llm=args.no_llm
    )
    executor = Executor(
        graph=graph_ret,
        vector=vector_ret,
        fulltext=fulltext_ret,
        llm=None if args.no_llm else llm,
        known_entities=known_entities,
    )
    guard_cfg = GuardrailConfig.from_app_config(cfg)
    report_path = _write_case_report(
        cases_path,
        report_dir,
        executor=executor,
        llm=llm,
        guard_cfg=guard_cfg,
        no_llm=args.no_llm,
        enable_triage=args.enable_triage,
        force_agentic=args.force_agentic,
        run_name=args.run_name,
    )
    graph_store.close()
    print(f"Report written to {report_path}")
    acc_path = report_dir / "poc_accuracy.json"
    acc = write_accuracy_summary(report_path, acc_path)
    print(f"Accuracy: {acc.correct}/{acc.total} = {acc.accuracy * 100:.1f}% → {acc_path}")


def _parse_run_cases(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run POC evaluation cases")
    parser.add_argument("--cases", default=None)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument(
        "--memory-graph", action="store_true", help="Use in-memory graph from seed triples"
    )
    parser.add_argument(
        "--neo4j",
        action="store_true",
        help="Force Neo4j graph backend (even with --no-llm).",
    )
    parser.add_argument("--seed-triples", default="data/processed/seed_triples.jsonl")
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--enable-triage",
        action="store_true",
        help="Use run_query with complexity triage (P3-PERF-01 / P3-EV-02).",
    )
    parser.add_argument(
        "--force-agentic",
        action="store_true",
        help="Force agentic path even when triage is enabled.",
    )
    parser.add_argument(
        "--run-name",
        default="poc_run",
        help="Output JSONL stem (default: poc_run).",
    )
    return parser.parse_args(argv)


def _write_case_report(
    cases_path,
    report_dir,
    *,
    executor,
    llm,
    guard_cfg,
    no_llm: bool,
    enable_triage: bool = False,
    force_agentic: bool = False,
    run_name: str = "poc_run",
):
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report_path = report_dir / f"{run_name}.jsonl"
    with report_path.open("w", encoding="utf-8") as out:
        for case in cases:
            row = run_case_row(
                case,
                executor=executor,
                llm=llm,
                guard_cfg=guard_cfg,
                no_llm=no_llm,
                enable_triage=enable_triage,
                force_agentic=force_agentic,
            )
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"[{case.get('id')}] {row.get('status') or 'error'} "
                f"route={row.get('route', '-')} steps={row.get('steps')} "
                f"graph={row.get('graph_evidence', 0)} — "
                f"{case['question'][:50]}"
            )
    return report_path


def score_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score a batch report against gold")
    parser.add_argument("--report", default="reports/poc_run.jsonl")
    parser.add_argument("--out", default="reports/poc_accuracy.json")
    args = parser.parse_args(argv)
    from agentic_graphrag.eval.scoring import write_accuracy_summary

    acc = write_accuracy_summary(resolve_path(args.report), resolve_path(args.out))
    print(json.dumps(acc.to_dict(), ensure_ascii=False, indent=2))


def eval_main(argv: list[str] | None = None) -> None:
    """P2-EV-04 — one-click comparison report from existing run artifacts."""
    args = _parse_eval(argv)
    cfg = get_config()
    from agentic_graphrag.eval.report import build_comparison_report, write_comparison_report

    agentic_path = resolve_path(args.agentic)
    baseline_path = resolve_path(args.baseline)
    cases_path = resolve_path(args.cases or cfg.eval.cases_path)
    out_dir = resolve_path(args.out or cfg.eval.report_dir)
    if not agentic_path.exists():
        print(f"Agentic run not found: {agentic_path}", file=sys.stderr)
        print("Run first: agr-run-cases --no-llm", file=sys.stderr)
        sys.exit(2)
    report = build_comparison_report(
        agentic_path=agentic_path,
        baseline_path=baseline_path if baseline_path.exists() else None,
        cases_path=cases_path if cases_path.exists() else None,
    )
    paths = write_comparison_report(report, out_dir, stem=args.stem)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report JSON → {paths['json']}")
    print(f"Report MD   → {paths['md']}")


def _parse_eval(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build Accuracy / evidence Recall / latency / cost comparison report "
            "from agentic + baseline JSONL run artifacts (P2-EV-04)"
        )
    )
    parser.add_argument("--agentic", default="reports/poc_run.jsonl")
    parser.add_argument("--baseline", default="reports/baseline_run.jsonl")
    parser.add_argument("--cases", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--stem", default="eval_comparison")
    return parser.parse_args(argv)
