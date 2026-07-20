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

from agentic_graphrag.config import AppConfig, Settings, get_config, get_settings, resolve_path
from agentic_graphrag.eval.baseline_corpus import ensure_embeddings, load_or_build_chunks
from agentic_graphrag.generation.trace import (
    Claim,
    QueryStatus,
    ReasoningChain,
    ReasoningStep,
    ToolCallTrace,
    validate_reasoning_chain,
)
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider, Message, MockLLMProvider, Tier
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.interfaces import ChunkRecord
from agentic_graphrag.stores.vector_store import InMemoryVectorStore

_MAX_BASELINE_CALLS = 1000
_MAX_BASELINE_TOKENS = 2_000_000
_MOCK_EMBED_DIM = 32

__all__ = [
    "BaselineResult",
    "BaselineVectorRAG",
    "build_baseline_pipeline",
    "ensure_embeddings",
    "load_or_build_chunks",
    "run_baseline_cases",
    "write_baseline_report",
]


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
        chain.steps.append(self._retrieval_step(question, hits))
        if not candidates:
            chain.honest_fallback("vector baseline retrieved no chunks")
        else:
            self._fill_answer(chain, question, candidates)
        chain.cost.latency_ms = int((time.perf_counter() - t0) * 1000)
        self._copy_budget(chain)
        return validate_reasoning_chain(chain)

    def _retrieval_step(self, question: str, hits: list[str]) -> ReasoningStep:
        return ReasoningStep(
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

    def _fill_answer(self, chain: ReasoningChain, question: str, candidates: list) -> None:
        hits = [c.id for c in candidates]
        use_llm = (
            self.allow_llm
            and self.llm is not None
            and not isinstance(self.llm, MockLLMProvider)
        )
        if use_llm:
            answer_text = self._llm_generate(question, candidates)
            chain.answer = answer_text
            chain.status = QueryStatus.ANSWERED
            chain.claims = [Claim(text=answer_text[:500], evidence_ids=hits[:5])]
            return
        self._extractive_answer(chain, candidates)

    def _extractive_answer(self, chain: ReasoningChain, candidates: list) -> None:
        snippets = [c.content.strip() for c in candidates[: self.top_k] if c.content.strip()]
        joined = "\n---\n".join(snippets[:5])
        chain.answer = (
            f"Based on retrieved evidence:\n{joined}"
            if joined
            else "无法基于现有知识回答。原因: empty vector hits。"
        )
        chain.status = QueryStatus.PARTIAL if joined else QueryStatus.NO_ANSWER
        chain.claims = [Claim(text=c.content[:300], evidence_ids=[c.id]) for c in candidates[:5]]
        chain.metadata["baseline_mode"] = "extractive_offline"

    def _copy_budget(self, chain: ReasoningChain) -> None:
        if self.llm is None or not getattr(self.llm, "budget", None):
            return
        snap = self.llm.budget.snapshot()  # type: ignore[union-attr]
        chain.cost.llm_calls = snap["llm_calls"]
        chain.cost.tokens = snap["total_tokens"]
        chain.cost.prompt_tokens = snap["prompt_tokens"]
        chain.cost.completion_tokens = snap["completion_tokens"]

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
    llm, allow_llm = _baseline_llm(allow_llm, settings, cfg)
    chunks = ensure_embeddings(chunks, llm, embeddings_path=epath if epath.exists() else None)
    store = InMemoryVectorStore()
    if chunks[0].embedding:
        store.ensure_collection(len(chunks[0].embedding))
    store.upsert(chunks)
    k = top_k if top_k is not None else cfg.retrieval.vector_top_k
    retriever = VectorRetriever(store, llm, top_k=k)
    return BaselineVectorRAG(retriever, llm, top_k=k, allow_llm=allow_llm), chunks


def _baseline_llm(
    allow_llm: bool, settings: Settings, cfg: AppConfig
) -> tuple[LLMProvider | MockLLMProvider, bool]:
    budget = BudgetTracker(max_llm_calls=_MAX_BASELINE_CALLS, max_tokens=_MAX_BASELINE_TOKENS)
    if allow_llm and settings.llm_api_key:
        from agentic_graphrag.config import build_llm_provider

        return (
            build_llm_provider(
                budget=budget,
                cache_dir=resolve_path(cfg.paths.cache_dir) / "llm",
                settings=settings,
                cfg=cfg,
            ),
            True,
        )
    return MockLLMProvider(embedding_dim=_MOCK_EMBED_DIM, budget=budget), False


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
