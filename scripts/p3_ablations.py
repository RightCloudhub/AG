#!/usr/bin/env python3
"""P3-EV ablation pack (D9): measure what each retrieval piece contributes.

Runs the G2 heldout set offline under three executor configurations and writes
per-ablation accuracy plus a summary:

  base        — RRF fusion over all three channels (the shipped config)
  no_graph    — graph tools disabled (vector + BM25 only)  → graph's contribution
  no_fusion   — plain concat instead of RRF                → fusion's contribution

The ``-critic`` ablation DESIGN_VS_IMPLEMENTATION names is live-only: the
critic node acts on LLM answers and is skipped entirely on the offline path
(``--no-llm`` returns before critic runs), so an offline run would measure
nothing. It belongs with the live reruns (C2/G2l), which this environment
cannot execute — recorded as such in the summary.

Usage:  PYTHONPATH=src python scripts/p3_ablations.py \
            [--cases evals/datasets/g2_heldout.jsonl]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASELINE = "reports/g2_heldout/agentic_run.jsonl"


def _run_variant(
    name: str,
    *,
    cases_path: Path,
    seed: Path,
    fusion: str | None,
    enable_graph: bool,
    out_dir: Path,
) -> dict:
    """One offline agentic run under a specific executor configuration."""
    from agentic_graphrag.agent.executor import Executor, ExecutorConfig
    from agentic_graphrag.agent.guardrails import GuardrailConfig
    from agentic_graphrag.cli.cases_run import (
        build_retrievers,
        known_entity_names,
        load_seed_triples,
        open_case_graph,
        run_case_row,
    )
    from agentic_graphrag.config import get_config, get_settings
    from agentic_graphrag.eval.scoring import write_accuracy_summary

    cfg = get_config()
    settings = get_settings()
    triples = load_seed_triples(seed)
    known = known_entity_names(triples)
    graph_store, _backend = open_case_graph(settings, use_memory=True, triples=triples)
    graph_ret, fulltext_ret, vector_ret, _llm = build_retrievers(
        cfg, settings, graph_store, no_llm=True
    )
    config = ExecutorConfig(
        fusion_method=fusion or cfg.retrieval.fusion_method,
        enable_graph_tools=enable_graph,
    )
    executor = Executor(
        graph=graph_ret,
        vector=vector_ret,
        fulltext=fulltext_ret,
        llm=None,
        known_entities=known,
        config=config,
    )
    guard_cfg = GuardrailConfig.from_app_config(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"{name}_run.jsonl"
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with report.open("w", encoding="utf-8") as f:
        for case in cases:
            row = run_case_row(
                case,
                executor=executor,
                llm=None,
                guard_cfg=guard_cfg,
                no_llm=True,
                enable_triage=False,
                force_agentic=True,
            )
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    acc = write_accuracy_summary(report, out_dir / f"{name}_accuracy.json")
    return {
        "variant": name,
        "fusion": config.fusion_method,
        "enable_graph_tools": config.enable_graph_tools,
        "accuracy": acc.accuracy,
        "correct": acc.correct,
        "total": acc.total,
        "report": str(report),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default="evals/datasets/g2_heldout.jsonl")
    ap.add_argument("--seed", default="data/processed/merged_triples.jsonl")
    ap.add_argument("--out", default="reports/p3_ablations")
    args = ap.parse_args()

    cases_path, seed = ROOT / args.cases, ROOT / args.seed
    out_dir = ROOT / args.out

    variants = [
        ("no_graph", {"fusion": None, "enable_graph": False}),
        ("no_fusion", {"fusion": "concat", "enable_graph": True}),
    ]
    results = []
    for name, kwargs in variants:
        print(f"=== ablation: {name} ===")
        results.append(
            _run_variant(name, cases_path=cases_path, seed=seed, out_dir=out_dir, **kwargs)
        )

    base_row = None
    base_path = ROOT / BASELINE
    if base_path.exists():
        rows = [
            json.loads(line)
            for line in base_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        base_row = {
            "variant": "base (shipped config)",
            "accuracy": round(sum(1 for r in rows if r.get("correct")) / max(len(rows), 1), 4),
            "total": len(rows),
            "report": str(base_path),
        }

    summary = {
        "task": "P3-EV ablations (D9)",
        "base": base_row,
        "ablations": results,
        "critic_ablation": (
            "live-only: the critic node is skipped on the offline path "
            "(--no-llm), so an offline -critic run measures nothing. Re-run "
            "with the live pack (C2/G2l) when an LLM key is available."
        ),
    }
    (out_dir / "ablations_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
