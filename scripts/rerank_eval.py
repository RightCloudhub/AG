"""Controlled reranking sweep using the existing case runner and scoring helpers.

Uses deterministic embeddings/answers and the pilot graph + chunks. This is
an explicitly instrumented experiment, not the production --no-llm profile.
HTTP is opt-in via --endpoint and any degradation invalidates that variant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.app.wiring import build_executor_for_service
from agentic_graphrag.cli.cases_run import known_entity_names, load_seed_triples, run_case_row
from agentic_graphrag.config import get_config, resolve_path
from agentic_graphrag.config_rerank import RerankConfig
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.eval.compare import compare_runs
from agentic_graphrag.eval.metrics import (
    evidence_recall_for_row,
    load_cases,
    load_jsonl,
    percentile,
)
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.retrieval.fusion import IdentityReranker, Reranker
from agentic_graphrag.retrieval.rerank_base import CrossEncoderReranker
from agentic_graphrag.stores.factory import create_bundle

POOLS = (10, 20, 30)
TOP_K = 10


class TimedReranker:
    def __init__(self, delegate: Reranker | None):
        self.delegate = delegate or IdentityReranker()
        self.elapsed_ms = 0.0

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        started = time.perf_counter()
        try:
            return self.delegate.rerank(query, candidates)
        finally:
            self.elapsed_ms += (time.perf_counter() - started) * 1000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="evals/datasets/g2_all.jsonl")
    parser.add_argument("--triples", default="data/processed/pilot_triples.jsonl")
    parser.add_argument("--chunks", default="data/processed/pilot_chunks.jsonl")
    parser.add_argument("--out", type=Path, default=Path("reports/rerank_runs"))
    parser.add_argument("--endpoint", help="Explicit TEI endpoint; omitted = HTTP unmeasured")
    parser.add_argument("--model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--limit", type=int, help="Smoke-test only; omit for the full dataset")
    return parser.parse_args()


def build_experiment(args: argparse.Namespace):
    from agentic_graphrag.config import Settings

    cfg = get_config().model_copy(deep=True)
    cfg.paths.processed_dir = str(args.out.resolve() / "runtime")
    settings = Settings(llm_api_key="", _env_file=None)
    bundle = create_bundle(
        "memory", "memory", cfg=cfg, settings=settings, load_bm25=False, load_embeddings=False
    )
    triples = load_seed_triples(resolve_path(args.triples))
    load_triples_into_graph(bundle.graph, triples, clear_first=True)
    embedder = MockLLMProvider()
    chunks = [
        ChunkRecord(
            chunk_id=r["chunk_id"],
            doc_id=r["doc_id"],
            text=r["text"],
            index=r["index"],
            embedding=embedder.embed(r["text"]),
        )
        for r in load_jsonl(resolve_path(args.chunks))
    ]
    bundle.vector.upsert(chunks)
    bundle.fulltext.index(chunks)
    return cfg, settings, bundle, known_entity_names(triples)


def build_executor(experiment, *, kind: str, pool: int, args: argparse.Namespace):
    base, settings, bundle, names = experiment
    cfg = base.model_copy(deep=True)
    cfg.retrieval.rerank = RerankConfig(
        kind=kind, candidate_pool=pool, endpoint=args.endpoint or "", model=args.model
    )
    cfg.retrieval.vector_top_k = cfg.retrieval.fulltext_top_k = pool
    cfg.retrieval.graph.max_paths = max(pool, cfg.retrieval.graph.max_paths)
    cfg.retrieval.graph.beam_width = max(pool, cfg.retrieval.graph.beam_width)
    exe = build_executor_for_service(
        bundle=bundle,
        cfg=cfg,
        settings=settings,
        allow_llm=True,
        known_entities=names,
        retrieval_cache=None,
        enable_cache=False,
    )
    # Equal recall and top_k for NONE too: isolate content scoring from pool size.
    exe.config.candidate_pool = pool
    exe.fusion_limit = TOP_K
    return exe


def run_variant(experiment, cases, *, kind, pool, args):
    rows = []
    guard = GuardrailConfig.from_app_config(experiment[0])
    for case in cases.values():
        exe = build_executor(experiment, kind=kind, pool=pool, args=args)
        timer = TimedReranker(exe.reranker)
        exe.reranker = timer
        started = time.perf_counter()
        row = run_case_row(
            case, executor=exe, llm=None, guard_cfg=guard, no_llm=True, force_agentic=True
        )
        row["latency_ms"] = (time.perf_counter() - started) * 1000
        row["rerank_ms"] = timer.elapsed_ms
        row["rerank_stats"] = (
            timer.delegate.telemetry() if isinstance(timer.delegate, CrossEncoderReranker) else {}
        )
        # Separate, fresh query: recall@10 of the original question, never answer text
        # or pre-cut tool hit lists; does not prewarm the timed answer execution.
        probe = build_executor(experiment, kind=kind, pool=pool, args=args)
        evidence, _ = probe.run(case["question"], allow_llm=False)
        row["recall_at_k"] = evidence_recall_for_row(
            {"case_id": case["id"], "chain": {"evidence": [c.model_dump() for c in evidence]}},
            cases,
            min_hops=0,
        )
        rows.append(row)
    return rows


def summarize(rows, base_rows, cases):
    paired = compare_runs(base_rows, rows, cases)
    recalls = [r["recall_at_k"] for r in rows if r["recall_at_k"] is not None]
    base_by_id = {r["case_id"]: r for r in base_rows}
    delta = [r["latency_ms"] - base_by_id[r["case_id"]]["latency_ms"] for r in rows]
    stage = [r["rerank_ms"] for r in rows]
    return {
        **paired["candidate"],
        "paired": paired["paired"],
        "recall_at_10": statistics.fmean(recalls) if recalls else None,
        "recall_n": len(recalls),
        "paired_added_p50_ms": percentile(delta, 50),
        "paired_added_p95_ms": percentile(delta, 95),
        "rerank_stage_p50_ms": percentile(stage, 50),
        "rerank_stage_p95_ms": percentile(stage, 95),
        "errors": sum(bool(r.get("error")) for r in rows),
        "fallbacks": sum(r["rerank_stats"].get("fallbacks", 0) for r in rows),
        "scored_pairs": sum(r["rerank_stats"].get("scored_pairs", 0) for r in rows),
        "requests": sum(r["rerank_stats"].get("requests", 0) for r in rows),
        "llm_tokens": sum(r["cost"].get("tokens", 0) for r in rows),
    }


def main() -> None:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = load_cases(resolve_path(args.cases))
    if args.limit:
        cases = dict(list(cases.items())[: args.limit])
    experiment = build_experiment(args)
    kinds = ["none", "lexical"] + (["http"] if args.endpoint else [])
    results: dict[str, Any] = {
        "mode": "deterministic answers and hash embeddings; live quality not established",
        "top_k": TOP_K,
        "graph_policy": "exempt",
        "model": args.model,
        "http_status": "requested" if args.endpoint else "unmeasured: no endpoint",
        "fixtures": {
            name: hashlib.sha256(resolve_path(getattr(args, name)).read_bytes()).hexdigest()
            for name in ("cases", "triples", "chunks")
        },
        "variants": {},
    }
    try:
        # Warm imports/runtime outside measurements, without cross-encoder requests.
        run_variant(experiment, dict(list(cases.items())[:1]), kind="none", pool=10, args=args)
        for pool in POOLS:
            base_rows = []
            for kind in kinds:
                name = f"{kind}_{pool}"
                rows = run_variant(experiment, cases, kind=kind, pool=pool, args=args)
                if kind == "none":
                    base_rows = rows
                (args.out / f"{name}.jsonl").write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
                )
                result = summarize(rows, base_rows, cases)
                results["variants"][name] = result
                print(
                    name,
                    result["correct"],
                    "/",
                    result["total"],
                    "recall@10=",
                    round(result["recall_at_10"], 4),
                    flush=True,
                )
        (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    finally:
        experiment[2].close()


if __name__ == "__main__":
    main()
