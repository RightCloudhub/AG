"""Run eval cases, score, and comparison report commands."""

from __future__ import annotations

import argparse
import json
import sys

from agentic_graphrag.cli._common import _open_graph_store
from agentic_graphrag.config import get_config, get_settings, resolve_path


def run_cases_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run POC evaluation cases")
    parser.add_argument("--cases", default=None)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument(
        "--memory-graph", action="store_true", help="Use in-memory graph from seed triples"
    )
    parser.add_argument(
        "--neo4j",
        action="store_true",
        help=(
            "Force Neo4j graph backend (even with --no-llm). "
            "Use after agr-build-graph populated Neo4j."
        ),
    )
    parser.add_argument("--seed-triples", default="data/processed/seed_triples.jsonl")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
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
    from agentic_graphrag.agent.loop import run_agentic_query
    from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
    from agentic_graphrag.knowledge.schema_check import Triple
    from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
    from agentic_graphrag.retrieval.fulltext import FulltextRetriever
    from agentic_graphrag.retrieval.graph import GraphRetriever
    from agentic_graphrag.retrieval.vector import VectorRetriever
    from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
    from agentic_graphrag.stores.interfaces import ChunkRecord
    from agentic_graphrag.stores.vector_store import InMemoryVectorStore, QdrantVectorStore

    seed_path = resolve_path(args.seed_triples)
    triples: list[Triple] = []
    if seed_path.exists():
        triples = [
            Triple.model_validate(json.loads(line))
            for line in seed_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    known_entities: list[str] = sorted(
        {t.head.name.strip() for t in triples if t.head.name.strip()}
        | {t.tail.name.strip() for t in triples if t.tail.name.strip()},
        key=lambda s: (-len(s), s.lower()),
    )

    # Graph backend selection:
    #   --memory-graph          → always in-memory (+ load seed)
    #   --neo4j                 → force Neo4j (for regression after build-graph)
    #   --no-llm (default)      → offline convenience: in-memory + seed (no Docker)
    #   live LLM (no flags)     → require Neo4j
    use_memory = args.memory_graph or (args.no_llm and not args.neo4j)
    graph_store, graph_backend = _open_graph_store(
        settings,
        memory=use_memory,
        allow_memory_fallback=False,
    )
    if graph_backend == "memory":
        if triples:
            load_triples_into_graph(graph_store, triples, clear_first=True)
        print(f"Loaded {len(triples)} seed triples into in-memory graph ({graph_store.counts()})")
    else:
        print(f"Using Neo4j graph store at {settings.neo4j_uri} ({graph_store.counts()})")

    graph_ret = GraphRetriever.from_config(graph_store, cfg)

    ft_store = BM25FulltextStore()
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    if ft_path.exists():
        ft_store.load(str(ft_path))
    fulltext_ret = FulltextRetriever(ft_store, top_k=cfg.retrieval.fulltext_top_k)

    vector_ret = None
    llm: LLMProvider | MockLLMProvider
    if args.no_llm:
        llm = MockLLMProvider()
        emb_path = resolve_path(f"{cfg.paths.indexes_dir}/embeddings.jsonl")
        if emb_path.exists():
            vstore = InMemoryVectorStore()
            chunks = []
            for line in emb_path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                chunks.append(
                    ChunkRecord(
                        chunk_id=item["chunk_id"],
                        doc_id=item["doc_id"],
                        text=item["text"],
                        index=item.get("index", 0),
                        embedding=item["embedding"],
                    )
                )
            if chunks:
                vstore.upsert(chunks)
                vector_ret = VectorRetriever(vstore, llm, top_k=cfg.retrieval.vector_top_k)
    else:
        from agentic_graphrag.config import build_llm_provider

        llm = build_llm_provider(
            cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
            settings=settings,
            cfg=cfg,
        )
        try:
            vstore = QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection)
            vector_ret = VectorRetriever(vstore, llm, top_k=cfg.retrieval.vector_top_k)
        except Exception as exc:
            print(f"Warning: vector store unavailable: {exc}", file=sys.stderr)

    executor = Executor(
        graph=graph_ret,
        vector=vector_ret,
        fulltext=fulltext_ret,
        llm=None if args.no_llm else llm,
        known_entities=known_entities,
    )
    guard_cfg = GuardrailConfig.from_app_config(cfg)

    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report_path = report_dir / "poc_run.jsonl"
    with report_path.open("w", encoding="utf-8") as out:
        for case in cases:
            q = case["question"]
            budget = guard_cfg.budget_tracker()
            try:
                chain = run_agentic_query(
                    q,
                    executor,
                    None if args.no_llm else llm,
                    guard_cfg=guard_cfg,
                    budget=budget,
                    allow_llm=not args.no_llm,
                )
                cost = chain.cost.model_dump()
                row = {
                    "case_id": case.get("id"),
                    "question": q,
                    "gold": case.get("gold_answer"),
                    "prediction": chain.answer,
                    "status": chain.status.value,
                    "steps": len(chain.steps),
                    "hop_count": max((s.hop for s in chain.steps), default=0),
                    "latency_ms": cost.get("latency_ms", 0),
                    "cost": cost,
                    "graph_evidence": sum(
                        1
                        for s in chain.steps
                        for tc in s.tool_calls
                        if tc.tool.startswith("graph") and tc.hits
                    ),
                    "explored_paths": chain.explored_paths[:20],
                    "chain": chain.model_dump(),
                }
            except Exception as exc:
                row = {
                    "case_id": case.get("id"),
                    "question": q,
                    "gold": case.get("gold_answer"),
                    "prediction": "",
                    "status": "error",
                    "steps": 0,
                    "hop_count": 0,
                    "latency_ms": 0,
                    "cost": {
                        "llm_calls": 0,
                        "tokens": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "latency_ms": 0,
                    },
                    "error": str(exc),
                }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"[{case.get('id')}] {row.get('status') or 'error'} "
                f"steps={row.get('steps')} graph={row.get('graph_evidence', 0)} — {q[:50]}"
            )

    graph_store.close()
    print(f"Report written to {report_path}")

    from agentic_graphrag.eval.scoring import write_accuracy_summary

    acc_path = report_dir / "poc_accuracy.json"
    acc = write_accuracy_summary(report_path, acc_path)
    print(f"Accuracy: {acc.correct}/{acc.total} = {acc.accuracy * 100:.1f}% → {acc_path}")

def score_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score a batch report against gold")
    parser.add_argument("--report", default="reports/poc_run.jsonl")
    parser.add_argument("--out", default="reports/poc_accuracy.json")
    args = parser.parse_args(argv)
    from agentic_graphrag.eval.scoring import write_accuracy_summary

    acc = write_accuracy_summary(resolve_path(args.report), resolve_path(args.out))
    print(json.dumps(acc.to_dict(), ensure_ascii=False, indent=2))

def eval_main(argv: list[str] | None = None) -> None:
    """P2-EV-04 — one-click comparison report from existing run artifacts.

    Does not re-execute systems. Produce runs first with::

        agr-run-cases --no-llm
        agr-run-baseline --no-llm
        agr-eval
    """
    parser = argparse.ArgumentParser(
        description=(
            "Build Accuracy / evidence Recall / latency / cost comparison report "
            "from agentic + baseline JSONL run artifacts (P2-EV-04)"
        )
    )
    parser.add_argument(
        "--agentic",
        default="reports/poc_run.jsonl",
        help="Agentic system run JSONL",
    )
    parser.add_argument(
        "--baseline",
        default="reports/baseline_run.jsonl",
        help="Baseline run JSONL (optional if missing)",
    )
    parser.add_argument(
        "--cases",
        default=None,
        help="Gold cases JSONL (for hops / gold_path evidence recall)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: configs eval.report_dir)",
    )
    parser.add_argument(
        "--stem",
        default="eval_comparison",
        help="Output filename stem (writes .json and .md)",
    )
    args = parser.parse_args(argv)
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

