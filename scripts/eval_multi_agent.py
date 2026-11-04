"""Paired multi-agent evaluation through the existing QueryApp and scoring path.

Seeds randomize case and configuration order, with caches disabled. They are
order seeds, not a claim that an upstream model supports deterministic sampling.
Live runs fail closed when credentials are absent; offline runs measure parity.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agentic_graphrag.cli.cases_run import (
    build_eval_app,
    known_entity_names,
    load_seed_triples,
    run_case_row_via_app,
)
from agentic_graphrag.config import get_config, get_settings
from agentic_graphrag.config_roles import RolesConfig
from agentic_graphrag.eval.metrics import load_jsonl, percentile, score_system_rows
from agentic_graphrag.eval.scoring import write_accuracy_summary
from agentic_graphrag.llm import response_cache


@contextmanager
def uncached_responses():
    """The eval profile disables retrieval/answer caches, but normally replays LLMs.

    Disable both response-cache operations for this isolated evaluation process,
    including calls from QueryApp's worker threads. Preserve existing cache files.
    """
    with (
        patch.object(response_cache, "read_cache", return_value=None),
        patch.object(response_cache, "write_cache", return_value=None),
    ):
        yield


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--no-llm", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--cases", type=Path, default=Path("evals/datasets/g2_all.jsonl"))
    parser.add_argument(
        "--seed-triples", type=Path, default=Path("data/processed/pilot_triples.jsonl")
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 23, 47])
    parser.add_argument("--parallel", nargs="+", type=int, choices=range(1, 17), default=[1, 3])
    parser.add_argument("--include-single", action="store_true")
    parser.add_argument("--enable-triage", action="store_true")
    parser.add_argument("--verifier-model", help="Override the verifier for this evaluation only")
    parser.add_argument(
        "--roles", type=Path, help="JSON role models and prices for this evaluation"
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out", type=Path, default=Path("reports/multi_agent_runs"))
    args = parser.parse_args(argv)
    if args.live:
        key = get_settings().llm_api_key
        if not key or "your-key" in key:
            parser.error("live evaluation requires LLM_API_KEY configured locally")
        cfg = get_config()
        if args.roles:
            cfg.roles = RolesConfig.model_validate_json(args.roles.read_text())
        if args.verifier_model:
            cfg.roles.verifier.model = args.verifier_model
        if (cfg.roles.verifier.model or cfg.llm.light_model) == (
            cfg.roles.researcher.model or cfg.llm.strong_model
        ):
            parser.error(
                "choose a distinct --verifier-model (researcher and verifier currently match)"
            )
    return args


def summarize(rows, name, *, offline):
    result = score_system_rows(rows, system=name).to_dict()
    result.pop("cases", None)
    result["errors"] = sum(row.get("status") == "error" for row in rows)
    costs = [0.0 if offline else row.get("metadata", {}).get("cost_usd") for row in rows]
    result["cost_usd"] = sum(costs) if all(cost is not None for cost in costs) else None
    result["tokens_complete"] = not result["errors"]
    result["status_counts"] = dict(Counter(row.get("status", "unknown") for row in rows))
    for key in ("prompt_tokens", "completion_tokens"):
        result[key] = sum(row.get("cost", {}).get(key, 0) for row in rows)
    wall = [row["wall_latency_ms"] for row in rows if "wall_latency_ms" in row]
    result["wall_latency_p50_ms"] = percentile(wall, 50) if wall else None
    result["wall_latency_p95_ms"] = percentile(wall, 95) if wall else None
    result["roles"] = role_usage(rows)
    return result


def role_usage(rows):
    roles = defaultdict(lambda: dict(calls=0, prompt_tokens=0, completion_tokens=0))
    for row in rows:
        for entry in row.get("metadata", {}).get("budget_ledger", []):
            if entry.get("kind") == "usage":
                for key in ("calls", "prompt_tokens", "completion_tokens"):
                    roles[entry["role"]][key] += entry.get(key, 0)
    return dict(roles)


def run_one(args, cases, *, name, parallel, seed):
    cfg = get_config()
    original = cfg.agent
    cfg.agent = original.model_copy(
        update={"mode": "single" if name == "single" else "multi", "max_parallel": parallel}
    )
    app = None
    rows = []
    failures = 0
    path = args.out / f"{name}-seed-{seed}.jsonl"
    try:
        app = build_eval_app(
            no_llm=args.no_llm,
            use_neo4j=False,
            seed_triples=args.seed_triples,
            enable_triage=args.enable_triage,
        )
        if app.allow_llm == args.no_llm:
            raise RuntimeError("requested evaluation mode does not match the runtime")
        with path.open("w") as output:
            for case in cases:
                started = time.perf_counter()
                with uncached_responses():
                    row = run_case_row_via_app(case, app=app, force_agentic=not args.enable_triage)
                row["wall_latency_ms"] = (time.perf_counter() - started) * 1000
                if row.get("error"):
                    row["latency_ms"] = row["wall_latency_ms"]
                rows.append(row)
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                output.flush()
                failures = failures + 1 if row.get("error") else 0
                if failures > 2:
                    raise RuntimeError(
                        f"Stopped after three consecutive query errors; inspect {path}"
                    )
    finally:
        cfg.agent = original
        if app is not None:
            app.close()
    lexicon = known_entity_names(load_seed_triples(args.seed_triples))
    write_accuracy_summary(path, path.with_suffix(".accuracy.json"), known_entities=lexicon)
    return rows


def main(argv=None):
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    cases = load_jsonl(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    variants = [(f"multi-{p}", p) for p in args.parallel]
    if args.include_single:
        variants.insert(0, ("single", 1))
    results, all_rows = [], {name: [] for name, _ in variants}
    for seed in args.seeds:
        order, configurations = list(cases), list(variants)
        rng = random.Random(seed)
        rng.shuffle(order)
        rng.shuffle(configurations)
        for name, parallel in configurations:
            rows = run_one(args, order, name=name, parallel=parallel, seed=seed)
            result = summarize(rows, name, offline=args.no_llm)
            results.append({"seed": seed, **result})
            all_rows[name].extend(rows)
            print(json.dumps(results[-1]), flush=True)
    summary = {
        "mode": "offline parity" if args.no_llm else "live",
        "seed_kind": "case and configuration order; model temperature from config",
        "cases": str(args.cases),
        "seeds": args.seeds,
        "configuration": get_config().model_dump(mode="json", include={"agent", "roles"}),
        "models": resolved_models(),
        "temperature": get_config().llm.temperature,
        "response_cache": "read and write disabled",
        "runs": results,
        "aggregate": [
            summarize(rows, name, offline=args.no_llm) for name, rows in all_rows.items()
        ],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


def resolved_models():
    cfg = get_config()
    return {
        name: cfg.roles.for_role(name).model
        or (cfg.llm.light_model if name in {"triage", "verifier"} else cfg.llm.strong_model)
        for name in ("triage", "researcher", "verifier", "writer")
    }


if __name__ == "__main__":
    main()
