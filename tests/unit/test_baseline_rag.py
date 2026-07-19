"""P2-EV-03 — baseline vector RAG on interim / temporary corpus."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_graphrag.eval.baseline_rag import (
    BaselineVectorRAG,
    build_baseline_pipeline,
    load_or_build_chunks,
    run_baseline_cases,
    write_baseline_report,
)
from agentic_graphrag.eval.scoring import score_report_file
from agentic_graphrag.generation.trace import QueryStatus, validate_reasoning_chain
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.interfaces import ChunkRecord
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


def test_load_chunks_from_interim_raw(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.md").write_text(
        "# Apex Holdings\n\nElena Varga is the CEO of Apex Holdings.\n",
        encoding="utf-8",
    )
    from agentic_graphrag.config import get_config

    chunks = load_or_build_chunks(
        chunks_path=tmp_path / "missing.jsonl",
        raw_docs_dir=raw,
        cfg=get_config(),
    )
    assert len(chunks) >= 1
    assert "Elena Varga" in chunks[0].text


def test_baseline_answer_offline_with_mock_vectors() -> None:
    llm = MockLLMProvider(embedding_dim=16)
    texts = [
        "Elena Varga is the CEO of Apex Holdings headquartered in Singapore.",
        "NovaTech Industries is a subsidiary of Apex Holdings.",
        "Helix Compute competes with NovaTech Industries.",
    ]
    chunks = [
        ChunkRecord(
            chunk_id=f"c{i}",
            doc_id="d",
            text=t,
            index=i,
            embedding=llm.embed(t),
        )
        for i, t in enumerate(texts)
    ]
    store = InMemoryVectorStore()
    store.ensure_collection(16)
    store.upsert(chunks)
    ret = VectorRetriever(store, llm, top_k=2)
    pipe = BaselineVectorRAG(ret, llm, top_k=2, allow_llm=False)
    chain = pipe.answer("Who is the CEO of Apex Holdings?")
    validate_reasoning_chain(chain)
    assert chain.route == "baseline"
    assert chain.schema_version
    assert chain.steps and chain.steps[0].tool_calls[0].tool == "vector_search"
    assert chain.status in {QueryStatus.PARTIAL, QueryStatus.ANSWERED, QueryStatus.NO_ANSWER}
    assert "Elena" in chain.answer or "retrieved" in chain.answer.lower() or chain.answer


def test_build_pipeline_on_repo_interim_data() -> None:
    pipe, chunks = build_baseline_pipeline(allow_llm=False, top_k=3)
    assert len(chunks) >= 1
    chain = pipe.answer("Who is the CEO of Apex Holdings?")
    assert chain.route == "baseline"
    assert chain.cost.latency_ms >= 0


def test_run_baseline_cases_and_score(tmp_path: Path) -> None:
    pipe, _ = build_baseline_pipeline(allow_llm=False, top_k=5)
    cases = [
        {
            "id": "b1",
            "question": "Who is the CEO of Apex Holdings?",
            "gold_answer": "Elena Varga",
        }
    ]
    results = run_baseline_cases(cases, pipe)
    out = tmp_path / "baseline_run.jsonl"
    write_baseline_report(results, out)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["system"] == "baseline_vector_rag"
    assert rows[0]["route"] == "baseline"
    report = score_report_file(out)
    assert report.total == 1
