"""Pure-vector and vector+rerank arms over the canonical passage corpus (B-05).

Each question gets a fresh BudgetTracker and client, so usage is query-scoped by
construction (never a cumulative snapshot, cf. the old baseline bug). Only passage
text is indexed; answers and support labels never reach this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.core.enums import MessageRole
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient, Message, Tier
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.eval.public.accounting import UsageLedger, ledger_from_budget
from agentic_graphrag.eval.public.adapters_base import AdapterResult, PrepareReport, error_result
from agentic_graphrag.eval.public.alignment import EvidenceRef, PassageResolver, align
from agentic_graphrag.eval.public.enums import ArmName, TrialStatus
from agentic_graphrag.eval.public.records import Passage, QuestionInput
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.extract_core import split_prompt
from agentic_graphrag.retrieval.fusion import Reranker
from agentic_graphrag.retrieval.vector import VectorRetriever
from agentic_graphrag.stores.vector_store import InMemoryVectorStore

ClientFactory = Callable[[BudgetTracker], LLMClient]
CHARS_PER_TOKEN = 4  # declared approximation for the context-token limit
EMBED_BATCH = 64
INDEX_MAX_CALLS = 10_000_000
INDEX_MAX_TOKENS = 10_000_000_000


@dataclass(frozen=True)
class VectorArmConfig:
    arm: ArmName
    reader_prompt: str
    top_k: int
    candidate_pool: int
    max_context_tokens: int
    max_llm_calls: int
    max_tokens: int


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * MILLISECONDS_PER_SECOND


class VectorArmAdapter:
    def __init__(
        self,
        config: VectorArmConfig,
        client_factory: ClientFactory,
        *,
        reranker: Reranker | None = None,
    ) -> None:
        if (config.arm is ArmName.VECTOR_RERANK) is (reranker is None):
            raise ValueError("vector_rerank needs a reranker; pure_vector must not have one")
        self.arm = config.arm
        self.config = config
        self.client_factory = client_factory
        self.reranker = reranker
        self.store = InMemoryVectorStore()
        self.resolver: PassageResolver | None = None
        self.passages: dict[str, Passage] = {}

    def prepare(self, passages: list[Passage], corpus_sha256: str) -> PrepareReport:
        t0 = time.perf_counter()
        budget = BudgetTracker(max_llm_calls=INDEX_MAX_CALLS, max_tokens=INDEX_MAX_TOKENS)
        client = self.client_factory(budget)
        chunks = [
            ChunkRecord(chunk_id=p.passage_id, doc_id=p.passage_id, text=p.index_text(), index=0)
            for p in passages
        ]
        for start in range(0, len(chunks), EMBED_BATCH):
            batch = chunks[start : start + EMBED_BATCH]
            for chunk, vec in zip(batch, client.embed_many([c.text for c in batch]), strict=True):
                chunk.embedding = vec
        if chunks and chunks[0].embedding:
            self.store.ensure_collection(len(chunks[0].embedding))
        self.store.upsert(chunks)
        self.passages = {p.passage_id: p for p in passages}
        self.resolver = PassageResolver(passages)
        usage = ledger_from_budget(None, budget.snapshot(), elapsed_ms=_ms(t0))
        return PrepareReport(
            arm=self.arm,
            passages=len(passages),
            corpus_sha256=corpus_sha256,
            usage=usage.model_copy(update={"scope": "indexing"}),
        )

    def answer(self, question: QuestionInput, *, run_seed: int) -> AdapterResult:
        del run_seed  # no provider seed control on this path; repeats are independent
        t0 = time.perf_counter()
        budget = BudgetTracker(
            max_llm_calls=self.config.max_llm_calls, max_tokens=self.config.max_tokens
        )
        try:
            return self._answer(question, budget, t0)
        except Exception as exc:  # noqa: BLE001 — every failure stays in the denominator
            usage = ledger_from_budget(None, budget.snapshot())
            return error_result(exc, elapsed_ms=_ms(t0), usage=usage)

    def _answer(self, question: QuestionInput, budget: BudgetTracker, t0: float) -> AdapterResult:
        if self.resolver is None:
            raise RuntimeError("prepare() must run before answer()")
        client = self.client_factory(budget)
        ranked, rerank_calls = self._retrieve(question.question, client)
        context = self._fit_context(ranked[: self.config.top_k])
        t_gen = time.perf_counter()
        prediction = client.complete(self._messages(question.question, context), tier=Tier.STRONG)
        retrieved_ids, report = align(self.resolver, [EvidenceRef(ref_id=c.id) for c in ranked])
        usage: UsageLedger = ledger_from_budget(None, budget.snapshot(), elapsed_ms=_ms(t0))
        return AdapterResult(
            prediction=prediction,
            status=TrialStatus.ANSWERED,
            retrieved_passage_ids=retrieved_ids,
            context_passage_ids=tuple(c.id for c in context),
            cited_passage_ids=None,
            alignment={"retrieved": report},
            usage=usage.model_copy(update={"rerank_calls": rerank_calls}),
            timings_ms={"total": _ms(t0), "generation": _ms(t_gen)},
        )

    def _retrieve(self, query: str, client: LLMClient) -> tuple[list[Candidate], int]:
        pool = self.config.candidate_pool if self.reranker else self.config.top_k
        retriever = VectorRetriever(self.store, client, top_k=pool)
        candidates = retriever.search(query, top_k=pool)
        if self.reranker is None:
            return candidates, 0
        return self.reranker.rerank(query, candidates), 1

    def _fit_context(self, candidates: list[Candidate]) -> list[Candidate]:
        """Keep ranked passages while they fit the declared context-token limit."""
        budget_chars = self.config.max_context_tokens * CHARS_PER_TOKEN
        kept: list[Candidate] = []
        for cand in candidates:
            budget_chars -= len(cand.content)
            if budget_chars < 0:
                break
            kept.append(cand)
        return kept

    def _messages(self, question: str, context: list[Candidate]) -> list[Message]:
        blocks = [f"[{i}] {c.content}" for i, c in enumerate(context, start=1)]
        filled = self.config.reader_prompt.format(passages="\n\n".join(blocks), question=question)
        system, user = split_prompt(filled)
        return [
            Message(role=MessageRole.SYSTEM, content=system),
            Message(role=MessageRole.USER, content=user),
        ]

    def close(self) -> None:
        self.store.close()
