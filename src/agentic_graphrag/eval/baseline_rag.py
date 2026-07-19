"""Pure vector RAG baseline (P2-EV-03).

Same corpus chunks + embeddings as agentic path, single-shot Top-K retrieval
and generation — no graph, no multi-hop agent loop. Supports:

- **offline** (``allow_llm=False``): MockLLM hash embeddings + extractive stitch
- **live**: real embed + LLM completion (when API key present)

Fairness: same cases file, same scoring helpers as agentic ``run-cases``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_graphrag.config import AppConfig, get_config, get_settings, resolve_path
from agentic_graphrag.generation.trace import (
    Claim,
    QueryStatus,
    ReasoningChain,
    ReasoningStep,
    ToolCallTrace,
    validate_reasoning_chain,
)
from agentic_graphrag.knowledge.ingest import chunk_document, load_documents_from_dir
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider, Message, MockLLMProvider, Tier
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.interfaces import ChunkRecord
from agentic_graphrag.stores.vector_store import InMemoryVectorStore


@dataclass
class BaselineResult:
    chain: ReasoningChain
    case_id: str | None = None
    gold: str | None = None

    def report_row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.chain.question,
            "gold": self.gold,
            "prediction": self.chain.answer,
            "status": self.chain.status.value,
            "route": self.chain.route,
            "system": "baseline_vector_rag",
            "steps": len(self.chain.steps),
            "hop_count": 1 if self.chain.steps else 0,
            "latency_ms": self.chain.cost.latency_ms,
            "cost": self.chain.cost.model_dump(mode="json"),
            "schema_version": self.chain.schema_version,
        }


class BaselineVectorRAG:
    """Single-round vector RAG pipeline."""

    def __init__(
        self,
        retriever: VectorRetriever,
        llm: LLMProvider | MockLLMProvider | None,
        *,
        top_k: int = 10,
        allow_llm: bool = False,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.top_k = top_k
        self.allow_llm = allow_llm

    def answer(self, question: str) -> ReasoningChain:
        t0 = time.perf_counter()
        chain = ReasoningChain(question=question, route="baseline")
        candidates = self.retriever.search(question, top_k=self.top_k)
        hits = [c.id for c in candidates]
        step = ReasoningStep(
            hop=1,
            sub_question=question,
            tool_calls=[
                ToolCallTrace(
                    tool="vector_search",
                    reason="baseline pure vector RAG",
                    args={"top_k": self.top_k},
                    hits=hits,
                )
            ],
            evidence_ids=hits,
            conclusion="",
            critic_action="sufficient",
        )
        chain.steps.append(step)

        if not candidates:
            chain.honest_fallback("vector baseline retrieved no chunks")
            chain.cost.latency_ms = int((time.perf_counter() - t0) * 1000)
            return validate_reasoning_chain(chain)

        if self.allow_llm and self.llm is not None and not isinstance(self.llm, MockLLMProvider):
            answer_text = self._llm_generate(question, candidates)
            chain.answer = answer_text
            chain.status = QueryStatus.ANSWERED
            chain.claims = [
                Claim(text=answer_text[:500], evidence_ids=hits[:5]),
            ]
        else:
            # Extractive offline baseline: concatenate top chunks (no graph reasoning)
            snippets = [c.content.strip() for c in candidates[: self.top_k] if c.content.strip()]
            joined = "\n---\n".join(snippets[:5])
            # Prefer short extract when gold-like entity names appear in first hit
            chain.answer = (
                f"Based on retrieved evidence:\n{joined}"
                if joined
                else "无法基于现有知识回答。原因: empty vector hits。"
            )
            chain.status = QueryStatus.PARTIAL if joined else QueryStatus.NO_ANSWER
            chain.claims = [
                Claim(text=c.content[:300], evidence_ids=[c.id]) for c in candidates[:5]
            ]
            chain.metadata["baseline_mode"] = "extractive_offline"

        chain.cost.latency_ms = int((time.perf_counter() - t0) * 1000)
        if self.llm is not None and getattr(self.llm, "budget", None):
            snap = self.llm.budget.snapshot()  # type: ignore[union-attr]
            chain.cost.llm_calls = snap["llm_calls"]
            chain.cost.tokens = snap["total_tokens"]
            chain.cost.prompt_tokens = snap["prompt_tokens"]
            chain.cost.completion_tokens = snap["completion_tokens"]
        return validate_reasoning_chain(chain)

    def _llm_generate(self, question: str, candidates: list) -> str:
        assert self.llm is not None
        ctx = "\n\n".join(f"[{c.id}] {c.content[:800]}" for c in candidates)
        messages = [
            Message(
                role="system",
                content=(
                    "You are a baseline RAG assistant. Answer only from the provided "
                    "passages. If insufficient, say you cannot answer."
                ),
            ),
            Message(
                role="user",
                content=f"Question: {question}\n\nPassages:\n{ctx}\n\nAnswer:",
            ),
        ]
        return self.llm.complete(messages, tier=Tier.STRONG)


def load_or_build_chunks(
    *,
    chunks_path: Path | None,
    raw_docs_dir: Path | None,
    cfg: AppConfig,
) -> list[ChunkRecord]:
    """Load chunks.jsonl, or chunk interim raw docs when missing (temp data path)."""
    if chunks_path and chunks_path.exists():
        out: list[ChunkRecord] = []
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            out.append(
                ChunkRecord(
                    chunk_id=item["chunk_id"],
                    doc_id=item["doc_id"],
                    text=item["text"],
                    index=int(item.get("index", 0)),
                    metadata=item.get("metadata") or {},
                    embedding=item.get("embedding"),
                )
            )
        return out

    docs_dir = raw_docs_dir or resolve_path(cfg.paths.raw_docs_dir)
    if not docs_dir.exists():
        raise FileNotFoundError(f"No chunks at {chunks_path} and raw docs dir missing: {docs_dir}")
    docs = load_documents_from_dir(docs_dir)
    chunks: list[ChunkRecord] = []
    for doc in docs:
        chunks.extend(
            chunk_document(
                doc,
                chunk_size=cfg.knowledge.chunk_size_chars,
                overlap=cfg.knowledge.chunk_overlap_chars,
            )
        )
    return chunks


def ensure_embeddings(
    chunks: list[ChunkRecord],
    llm: LLMProvider | MockLLMProvider,
    *,
    embeddings_path: Path | None = None,
) -> list[ChunkRecord]:
    """Attach embeddings from cache file or compute via llm.embed."""
    by_id: dict[str, list[float]] = {}
    if embeddings_path and embeddings_path.exists():
        for line in embeddings_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("embedding"):
                by_id[item["chunk_id"]] = list(item["embedding"])

    for ch in chunks:
        if ch.embedding:
            continue
        if ch.chunk_id in by_id:
            ch.embedding = by_id[ch.chunk_id]
        else:
            ch.embedding = llm.embed(ch.text)
    return chunks


def build_baseline_pipeline(
    *,
    cfg: AppConfig | None = None,
    chunks_path: str | Path | None = None,
    raw_docs_dir: str | Path | None = None,
    embeddings_path: str | Path | None = None,
    allow_llm: bool = False,
    top_k: int | None = None,
) -> tuple[BaselineVectorRAG, list[ChunkRecord]]:
    """Compose offline-capable baseline over interim or provided data."""
    cfg = cfg or get_config()
    settings = get_settings()
    cpath = resolve_path(chunks_path or f"{cfg.paths.processed_dir}/chunks.jsonl")
    rpath = resolve_path(raw_docs_dir) if raw_docs_dir else resolve_path(cfg.paths.raw_docs_dir)
    epath = resolve_path(embeddings_path or f"{cfg.paths.indexes_dir}/embeddings.jsonl")

    chunks = load_or_build_chunks(chunks_path=cpath, raw_docs_dir=rpath, cfg=cfg)
    if not chunks:
        raise RuntimeError("Baseline corpus is empty — provide chunks or raw docs")

    budget = BudgetTracker(max_llm_calls=1000, max_tokens=2_000_000)
    if allow_llm and settings.llm_api_key:
        from agentic_graphrag.config import build_llm_provider

        llm: LLMProvider | MockLLMProvider = build_llm_provider(
            budget=budget,
            cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
            settings=settings,
            cfg=cfg,
        )
    else:
        llm = MockLLMProvider(embedding_dim=32, budget=budget)
        allow_llm = False

    chunks = ensure_embeddings(chunks, llm, embeddings_path=epath if epath.exists() else None)
    store = InMemoryVectorStore()
    if chunks[0].embedding:
        store.ensure_collection(len(chunks[0].embedding))
    store.upsert(chunks)
    k = top_k if top_k is not None else cfg.retrieval.vector_top_k
    retriever = VectorRetriever(store, llm, top_k=k)
    pipeline = BaselineVectorRAG(retriever, llm, top_k=k, allow_llm=allow_llm)
    return pipeline, chunks


def run_baseline_cases(
    cases: list[dict[str, Any]],
    pipeline: BaselineVectorRAG,
) -> list[BaselineResult]:
    results: list[BaselineResult] = []
    for case in cases:
        chain = pipeline.answer(case["question"])
        results.append(
            BaselineResult(
                chain=chain,
                case_id=case.get("id"),
                gold=case.get("gold_answer"),
            )
        )
    return results


def write_baseline_report(
    results: list[BaselineResult],
    out_path: Path,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.report_row(), ensure_ascii=False) + "\n")
    return out_path
