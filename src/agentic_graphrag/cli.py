"""CLI entrypoints for POC scripts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agentic_graphrag.config import get_config, get_settings, resolve_path
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.ingest import chunk_document, load_documents_from_dir
from agentic_graphrag.knowledge.schema_check import Triple, load_schema, validate_triples
from agentic_graphrag.stores.doc_store import FileDocStore
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import ChunkRecord


def _ensure_dirs(cfg) -> None:
    for key in ("data_dir", "raw_docs_dir", "processed_dir", "cache_dir", "indexes_dir"):
        resolve_path(getattr(cfg.paths, key)).mkdir(parents=True, exist_ok=True)


def _neo4j_unavailable_hint(uri: str, exc: BaseException) -> str:
    return (
        f"Neo4j unavailable at {uri}: {exc}\n"
        "  Offline dry-run:  agr-build-graph --triples … --no-llm [--memory-graph]\n"
        "  Start Neo4j:      docker compose up -d\n"
        "  Offline eval:     agr-run-cases --no-llm  (loads seed triples itself)"
    )


def _open_graph_store(
    settings: Any,
    *,
    memory: bool = False,
    allow_memory_fallback: bool = False,
) -> tuple[Any, str]:
    """Open a GraphStore.

    - ``memory=True`` → always InMemoryGraphStore (process-local).
    - else try Neo4j; on failure optionally fall back to memory (seed / offline paths).
    """
    if memory:
        from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

        return InMemoryGraphStore(), "memory"

    from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore

    store: Any = None
    try:
        store = Neo4jGraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        store.ping()
        return store, "neo4j"
    except Exception as exc:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        if allow_memory_fallback:
            from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

            print(
                f"Warning: Neo4j unavailable at {settings.neo4j_uri} ({exc}); "
                "falling back to in-memory graph (process-local, not persisted).",
                file=sys.stderr,
            )
            return InMemoryGraphStore(), "memory"
        print(_neo4j_unavailable_hint(settings.neo4j_uri, exc), file=sys.stderr)
        sys.exit(1)


def ingest_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest and chunk documents")
    parser.add_argument("--input", default=None, help="Raw docs directory")
    parser.add_argument("--out", default=None, help="Processed chunks JSONL path")
    args = parser.parse_args(argv)
    cfg = get_config()
    _ensure_dirs(cfg)
    input_dir = resolve_path(args.input or cfg.paths.raw_docs_dir)
    out_path = resolve_path(args.out or f"{cfg.paths.processed_dir}/chunks.jsonl")

    docs = load_documents_from_dir(input_dir)
    doc_store = FileDocStore(resolve_path(cfg.paths.processed_dir) / "docs")
    chunks: list[ChunkRecord] = []
    for doc in docs:
        doc_store.save(doc)
        chunks.extend(
            chunk_document(
                doc,
                chunk_size=cfg.knowledge.chunk_size_chars,
                overlap=cfg.knowledge.chunk_overlap_chars,
            )
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(
                json.dumps(
                    {
                        "chunk_id": ch.chunk_id,
                        "doc_id": ch.doc_id,
                        "text": ch.text,
                        "index": ch.index,
                        "metadata": ch.metadata,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"Ingested {len(docs)} docs → {len(chunks)} chunks → {out_path}")


def build_graph_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Extract triples and load graph (Neo4j by default; --memory-graph for offline)"
    )
    parser.add_argument("--chunks", default=None, help="chunks.jsonl path")
    parser.add_argument("--triples", default=None, help="Optional precomputed triples JSONL")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM; load --triples only")
    parser.add_argument(
        "--memory-graph",
        action="store_true",
        help="Use in-memory graph (no Neo4j; process-local dry-run / offline seed validation)",
    )
    parser.add_argument("--no-clear", action="store_true", help="Do not clear graph first")
    args = parser.parse_args(argv)
    cfg = get_config()
    settings = get_settings()
    schema = load_schema(resolve_path(cfg.knowledge.schema_path))

    triples: list[Triple] = []
    if args.triples:
        for line in resolve_path(args.triples).read_text(encoding="utf-8").splitlines():
            if line.strip():
                triples.append(Triple.model_validate(json.loads(line)))
    elif not args.no_llm:
        from agentic_graphrag.knowledge.extraction import extract_from_chunks
        from agentic_graphrag.llm.budget import BudgetTracker
        from agentic_graphrag.llm.provider import LLMProvider

        chunks_path = resolve_path(args.chunks or f"{cfg.paths.processed_dir}/chunks.jsonl")
        chunks = [
            ChunkRecord(**json.loads(line))
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        budget = BudgetTracker(max_llm_calls=10_000, max_tokens=10_000_000)
        llm = LLMProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            strong_model=cfg.llm.strong_model,
            light_model=cfg.llm.light_model,
            embedding_model=cfg.llm.embedding_model,
            budget=budget,
            cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
        )
        accepted, rejected = extract_from_chunks(
            chunks,
            schema,
            llm,
            confidence_threshold=cfg.knowledge.extract_confidence_threshold,
        )
        triples = accepted
        reject_path = resolve_path(f"{cfg.paths.processed_dir}/rejected_triples.jsonl")
        with reject_path.open("w", encoding="utf-8") as f:
            for t, reason in rejected:
                f.write(json.dumps({"triple": t.model_dump(), "reason": reason}, ensure_ascii=False) + "\n")
        print(f"Extracted accepted={len(accepted)} rejected={len(rejected)}")
        triples_path = resolve_path(f"{cfg.paths.processed_dir}/triples.jsonl")
        with triples_path.open("w", encoding="utf-8") as f:
            for t in triples:
                f.write(json.dumps(t.model_dump(), ensure_ascii=False) + "\n")
    else:
        print("--no-llm requires --triples", file=sys.stderr)
        sys.exit(2)

    validated = validate_triples(triples, schema)
    triples = validated.accepted
    print(
        f"Schema-valid triples: {len(triples)} (rejected {len(validated.rejected)})",
        flush=True,
    )

    # Seed / --no-llm path is offline-friendly: prefer Neo4j when up, else memory.
    # LLM extract path requires Neo4j (no silent fallback).
    store, backend = _open_graph_store(
        settings,
        memory=args.memory_graph,
        allow_memory_fallback=args.no_llm,
    )
    try:
        stats = load_triples_into_graph(store, triples, clear_first=not args.no_clear)
        stats["backend"] = backend
        print(json.dumps(stats, indent=2), flush=True)
        if backend == "memory":
            print(
                "Note: in-memory graph is process-local. "
                "Offline eval does not need this step — use: agr-run-cases --no-llm",
                file=sys.stderr,
                flush=True,
            )
    finally:
        store.close()


def index_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build vector + BM25 indexes")
    parser.add_argument("--chunks", default=None)
    parser.add_argument("--memory-vector", action="store_true", help="Use in-memory vector (no Qdrant)")
    parser.add_argument("--no-embed", action="store_true", help="Skip embeddings (BM25 only)")
    args = parser.parse_args(argv)
    cfg = get_config()
    settings = get_settings()
    chunks_path = resolve_path(args.chunks or f"{cfg.paths.processed_dir}/chunks.jsonl")
    chunks = [
        ChunkRecord(**json.loads(line))
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    ft = BM25FulltextStore()
    n = ft.index(chunks)
    ft_path = resolve_path(f"{cfg.paths.indexes_dir}/bm25.json")
    ft.save(str(ft_path))
    print(f"BM25 indexed {n} chunks → {ft_path}")

    if args.no_embed:
        return

    from agentic_graphrag.llm.provider import LLMProvider
    from agentic_graphrag.stores.vector_store import InMemoryVectorStore, QdrantVectorStore

    llm = LLMProvider(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        embedding_model=cfg.llm.embedding_model,
        cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
    )
    for ch in chunks:
        ch.embedding = llm.embed(ch.text)

    if args.memory_vector:
        store = InMemoryVectorStore()
        store.ensure_collection(len(chunks[0].embedding) if chunks and chunks[0].embedding else 8)
        store.upsert(chunks)
        # Persist embeddings for offline reuse
        emb_path = resolve_path(f"{cfg.paths.indexes_dir}/embeddings.jsonl")
        with emb_path.open("w", encoding="utf-8") as f:
            for ch in chunks:
                f.write(
                    json.dumps(
                        {"chunk_id": ch.chunk_id, "embedding": ch.embedding, "text": ch.text, "doc_id": ch.doc_id, "index": ch.index},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        print(f"In-memory vectors prepared → {emb_path}")
    else:
        store = QdrantVectorStore(settings.qdrant_url, settings.qdrant_collection)
        if chunks and chunks[0].embedding:
            store.ensure_collection(len(chunks[0].embedding))
        n_vec = store.upsert(chunks)
        store.close()
        print(f"Qdrant upserted {n_vec} vectors")


def run_cases_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run POC evaluation cases")
    parser.add_argument("--cases", default=None)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--memory-graph", action="store_true", help="Use in-memory graph from seed triples")
    parser.add_argument(
        "--neo4j",
        action="store_true",
        help="Force Neo4j graph backend (even with --no-llm). Use after agr-build-graph populated Neo4j.",
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
    from agentic_graphrag.llm.budget import BudgetTracker
    from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
    from agentic_graphrag.retrieval.fulltext import FulltextRetriever
    from agentic_graphrag.retrieval.graph import GraphRetriever
    from agentic_graphrag.retrieval.vector import VectorRetriever
    from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
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
        {
            t.head.name.strip()
            for t in triples
            if t.head.name.strip()
        }
        | {
            t.tail.name.strip()
            for t in triples
            if t.tail.name.strip()
        },
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
        print(
            f"Loaded {len(triples)} seed triples into in-memory graph "
            f"({graph_store.counts()})"
        )
    else:
        print(f"Using Neo4j graph store at {settings.neo4j_uri} ({graph_store.counts()})")

    graph_ret = GraphRetriever(
        graph_store,
        max_neighbors_per_layer=cfg.retrieval.graph.max_neighbors_per_layer,
        max_paths=cfg.retrieval.graph.max_paths,
        default_neighbor_hops=cfg.retrieval.graph.max_hop_neighbors,
        default_path_hops=cfg.retrieval.graph.max_path_hops,
    )

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
        llm = LLMProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            strong_model=cfg.llm.strong_model,
            light_model=cfg.llm.light_model,
            embedding_model=cfg.llm.embedding_model,
            cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
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
    guard_cfg = GuardrailConfig(
        max_hops=cfg.guardrails.max_hops,
        max_llm_calls=cfg.guardrails.max_llm_calls,
        max_tokens=cfg.guardrails.max_tokens_per_query,
    )

    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report_path = report_dir / "poc_run.jsonl"
    with report_path.open("w", encoding="utf-8") as out:
        for case in cases:
            q = case["question"]
            budget = BudgetTracker(
                max_llm_calls=guard_cfg.max_llm_calls,
                max_tokens=guard_cfg.max_tokens,
            )
            try:
                chain = run_agentic_query(
                    q,
                    executor,
                    None if args.no_llm else llm,
                    guard_cfg=guard_cfg,
                    budget=budget,
                    allow_llm=not args.no_llm,
                    recursion_limit=cfg.guardrails.recursion_limit,
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
    print(
        f"Accuracy: {acc.correct}/{acc.total} = {acc.accuracy * 100:.1f}% → {acc_path}"
    )


def score_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Score a batch report against gold")
    parser.add_argument("--report", default="reports/poc_run.jsonl")
    parser.add_argument("--out", default="reports/poc_accuracy.json")
    args = parser.parse_args(argv)
    from agentic_graphrag.eval.scoring import write_accuracy_summary

    acc = write_accuracy_summary(resolve_path(args.report), resolve_path(args.out))
    print(json.dumps(acc.to_dict(), ensure_ascii=False, indent=2))


def spotcheck_main(argv: list[str] | None = None) -> None:
    """Generate triple spot-check sample for P1-KG-05 / G1→G2 live extract audit."""
    parser = argparse.ArgumentParser(description="Build triple spot-check sample")
    parser.add_argument("--triples", default="data/processed/seed_triples.jsonl")
    parser.add_argument("--out", default="reports/triple_spotcheck.jsonl")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--mode",
        choices=("seed", "llm"),
        default="seed",
        help="seed: schema-valid→correct (POC baseline); llm: pending_human for manual audit",
    )
    parser.add_argument(
        "--schema",
        default="configs/schema/domain_v0.yaml",
        help="Schema YAML path",
    )
    args = parser.parse_args(argv)
    from agentic_graphrag.config import resolve_path as rp
    from agentic_graphrag.knowledge.schema_check import Triple, load_schema, validate_triple

    triples_path = rp(args.triples)
    if not triples_path.exists():
        print(f"Triples file not found: {triples_path}", file=sys.stderr)
        sys.exit(2)
    schema = load_schema(rp(args.schema))
    out_path = rp(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for line in triples_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        t = Triple.model_validate(json.loads(line))
        reason = validate_triple(t, schema)
        if args.mode == "seed":
            # Seed triples are curated gold for interim corpus → label correct when schema-valid
            label = "correct" if reason is None else "incorrect"
            label_source = "seed_baseline_schema_valid"
        else:
            # LLM extract: leave for human audit; schema failure is already incorrect
            if reason is not None:
                label = "incorrect"
                label_source = "schema_reject"
            else:
                label = "pending_human"
                label_source = "llm_extract_pending_human"
        rows.append(
            {
                "head": t.head.model_dump(),
                "relation": t.relation,
                "tail": t.tail.model_dump(),
                "confidence": t.confidence,
                "source_span": t.source_span,
                "source_doc_id": t.source_doc_id,
                "source_chunk_id": t.source_chunk_id,
                "schema_ok": reason is None,
                "schema_reason": reason,
                "human_label": label,
                "label_source": label_source,
            }
        )
        if len(rows) >= args.limit:
            break

    labeled = [r for r in rows if r["human_label"] in ("correct", "incorrect")]
    pending = sum(1 for r in rows if r["human_label"] == "pending_human")
    correct = sum(1 for r in labeled if r["human_label"] == "correct")
    rate = correct / len(labeled) if labeled else None
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "mode": args.mode,
        "sample_size": len(rows),
        "correct": correct,
        "incorrect": sum(1 for r in labeled if r["human_label"] == "incorrect"),
        "pending_human": pending,
        "correct_rate": round(rate, 4) if rate is not None else None,
        "correct_rate_pct": round(rate * 100, 2) if rate is not None else None,
        "label_source": (
            "seed_baseline (schema-valid seed triples treated as correct)"
            if args.mode == "seed"
            else "llm extract: set human_label correct|incorrect then re-score"
        ),
        "target_correct_rate_pct": 70.0,
        "note": (
            "P1-KG-05 seed baseline"
            if args.mode == "seed"
            else "G1→G2 live extract audit: fill human_label, then: python -m agentic_graphrag score-spotcheck"
        ),
        "path": str(out_path),
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def score_spotcheck_main(argv: list[str] | None = None) -> None:
    """Re-score a spotcheck JSONL after human labels are filled in."""
    parser = argparse.ArgumentParser(description="Score triple spot-check after human labeling")
    parser.add_argument("--in", dest="inp", default="reports/triple_spotcheck_llm.jsonl")
    parser.add_argument("--out", default=None, help="Summary JSON path (default: <in>.summary.json)")
    args = parser.parse_args(argv)
    inp = resolve_path(args.inp)
    if not inp.exists():
        print(f"Not found: {inp}", file=sys.stderr)
        sys.exit(2)
    rows = [
        json.loads(line)
        for line in inp.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pending = [r for r in rows if r.get("human_label") == "pending_human"]
    labeled = [r for r in rows if r.get("human_label") in ("correct", "incorrect")]
    correct = sum(1 for r in labeled if r["human_label"] == "correct")
    rate = correct / len(labeled) if labeled else None
    summary = {
        "sample_size": len(rows),
        "labeled": len(labeled),
        "pending_human": len(pending),
        "correct": correct,
        "incorrect": len(labeled) - correct,
        "correct_rate": round(rate, 4) if rate is not None else None,
        "correct_rate_pct": round(rate * 100, 2) if rate is not None else None,
        "pass_g1_extract_gate": bool(rate is not None and rate >= 0.70 and not pending),
        "target_correct_rate_pct": 70.0,
        "path": str(inp),
    }
    out = resolve_path(args.out) if args.out else inp.with_suffix(".summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if pending:
        print(f"Warning: {len(pending)} rows still pending_human", file=sys.stderr)
    if rate is not None and rate < 0.70:
        print(f"Below 70% extract gate: {rate * 100:.1f}%", file=sys.stderr)
        sys.exit(3)


def query_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Single query (agentic)")
    parser.add_argument("question")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--memory-graph", action="store_true", help="Use in-memory graph from seed triples")
    parser.add_argument("--neo4j", action="store_true", help="Force Neo4j graph backend")
    parser.add_argument("--seed-triples", default="data/processed/seed_triples.jsonl")
    args = parser.parse_args(argv)
    # Reuse run_cases machinery for one question
    cases_path = resolve_path("data/processed/_single_case.jsonl")
    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases_path.write_text(
        json.dumps({"id": "adhoc", "question": args.question, "gold_answer": ""}, ensure_ascii=False) + "\n",
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


if __name__ == "__main__":
    # Dispatch by script name if needed
    print("Use: agr-ingest | agr-build-graph | agr-index | agr-run-cases | agr-query", file=sys.stderr)
    sys.exit(1)
