"""Every LLM role and embedding draws on one per-query meter (GOV-3, GOV-4, OBS-02)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.query_run import build_query_deps
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.config import Settings, get_config
from agentic_graphrag.core.enums import LLMRole, MessageRole
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import (
    LLM_BY_ROLE_KEY,
    BudgetExceeded,
    BudgetTracker,
    apply_budget,
    role_scope,
)
from agentic_graphrag.llm.circuit import ROUTE_EMBED
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.stores.interfaces import ChunkRecord

THREADS = 8
CAP = 5
# No entity the rules can link, so the executor asks the LLM for a tool plan.
QUESTION = "what are the overall themes across all documents?"
FACT = "the overall themes across all documents are supply and demand"


class _Reply(BaseModel):
    ok: bool = True


def _msgs() -> list[Message]:
    return [Message(role=MessageRole.USER, content="hi")]


def test_query_tracker_uses_the_configured_limits() -> None:
    cfg = get_config().model_copy(deep=True)
    cfg.guardrails.max_llm_calls = 7
    cfg.guardrails.max_tokens_per_query = 1234
    svc = QueryService.create_offline(cfg=cfg)
    try:
        tracker = svc.multi_budget.query_tracker()
        assert (tracker.max_llm_calls, tracker.max_tokens) == (7, 1234)
        guard = GuardrailConfig.from_app_config(cfg)
        assert (guard.max_llm_calls, guard.max_tokens) == (7, 1234)
    finally:
        svc.close()


def test_concurrent_reservations_never_exceed_the_cap() -> None:
    budget = BudgetTracker(max_llm_calls=CAP)
    granted: list[int] = []
    barrier = threading.Barrier(THREADS)

    def worker() -> None:
        barrier.wait()
        for _ in range(CAP):
            try:
                budget.reserve_call()
            except BudgetExceeded:
                return
            granted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == CAP
    assert budget.llm_calls == CAP


def test_released_reservation_frees_its_slot() -> None:
    budget = BudgetTracker(max_llm_calls=1)
    budget.reserve_call()
    with pytest.raises(BudgetExceeded):
        budget.check()
    budget.release_call()
    assert budget.llm_calls == 0
    budget.check()


def test_settling_a_paid_call_never_raises() -> None:
    """A response already paid for must not be thrown away at the cap."""
    budget = BudgetTracker(max_llm_calls=1, max_tokens=10)
    budget.reserve_call()
    budget.settle_call(prompt_tokens=100, completion_tokens=100)
    assert budget.total_tokens == 200
    budget.record_embedding(tokens=5)
    assert budget.embed_calls == 1
    with pytest.raises(BudgetExceeded):
        budget.check()


def test_record_call_still_enforces_the_cap_atomically() -> None:
    budget = BudgetTracker(max_llm_calls=1)
    budget.record_call(1, 1)
    with pytest.raises(BudgetExceeded):
        budget.record_call(1, 1)
    assert budget.llm_calls == 1


def test_calls_are_attributed_to_the_role_in_scope() -> None:
    budget = BudgetTracker()
    with role_scope(LLMRole.PLANNER):
        budget.record_call(10, 2)
    with role_scope(LLMRole.CRITIC):
        budget.record_call(3, 1)
        budget.record_remote(40, failed=False)
    budget.record_remote(9, failed=True, role=LLMRole.EMBEDDING)

    usage = budget.role_usage()

    assert usage["planner"]["calls"] == 1 and usage["planner"]["prompt_tokens"] == 10
    assert usage["critic"]["llm_ms"] == 40 and usage["critic"]["failures"] == 0
    assert usage["embedding"]["failures"] == 1 and usage["embedding"]["llm_ms"] == 9


def test_role_scope_is_per_thread_for_concurrent_roles() -> None:
    budget = BudgetTracker()
    barrier = threading.Barrier(2)

    def worker(role: LLMRole) -> None:
        with role_scope(role):
            barrier.wait()
            budget.record_call(1, 1)

    threads = [
        threading.Thread(target=worker, args=(role,))
        for role in (LLMRole.CRITIC, LLMRole.REGISTER_VERIFY)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    usage = budget.role_usage()
    assert usage["critic"]["calls"] == 1 and usage["register_verify"]["calls"] == 1


def test_complete_structured_records_role_and_parse_failures() -> None:
    budget = BudgetTracker()
    replies = iter(["not json", json.dumps({"ok": True})])

    class Flaky(MockLLMProvider):
        def complete(self, messages, **kwargs) -> str:
            super().complete(messages, **kwargs)
            return next(replies)

    reply = complete_structured(Flaky(budget=budget), _msgs(), _Reply, role=LLMRole.ENTAILMENT)

    assert reply.ok is True
    usage = budget.role_usage()["entailment"]
    assert usage["calls"] == 2 and usage["parse_failures"] == 1


def test_apply_budget_writes_cost_and_role_usage_once() -> None:
    budget = BudgetTracker()
    with role_scope(LLMRole.PLANNER):
        budget.record_call(10, 2)
    budget.record_embedding(tokens=3)
    chain = ReasoningChain(question="q")

    apply_budget(chain, budget)

    assert chain.cost.llm_calls == 1
    assert chain.cost.embed_calls == 1
    assert chain.cost.tokens == 15
    assert chain.metadata[LLM_BY_ROLE_KEY]["planner"]["calls"] == 1


def test_apply_budget_adds_no_metadata_without_llm_use() -> None:
    chain = ReasoningChain(question="q")
    apply_budget(chain, BudgetTracker())
    assert LLM_BY_ROLE_KEY not in (chain.metadata or {})
    apply_budget(chain, None)
    assert chain.cost.llm_calls == 0


class _Gateway:
    """Fake HTTP layer answering each LLM role with schema-valid JSON."""

    def __init__(self) -> None:
        self.chat = 0
        self.embeddings = 0
        self.fail_paths: set[str] = set()

    def post(self, path: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if path in self.fail_paths:
            raise ConnectionError("gateway down")
        if path == "/embeddings":
            self.embeddings += 1
            assert kwargs["route"] == ROUTE_EMBED
            return {"data": [{"embedding": [0.1] * 8}], "usage": {"total_tokens": 3}}
        self.chat += 1
        content = json.dumps(_reply_for(payload["messages"][0]["content"]))
        usage = {"prompt_tokens": 7, "completion_tokens": 3}
        return {"choices": [{"message": {"content": content}}], "usage": usage}


def _reply_for(system: str) -> dict[str, Any]:
    if "Executor" in system:
        return {"tool_calls": [{"tool": "vector_search", "args": {"query": "themes"}}]}
    if "Planner" in system:
        return {"sub_questions": [{"id": "sq1", "text": QUESTION}]}
    if "Critic" in system:
        return {"action": "sufficient", "sub_answered": True}
    if "Answer" in system:
        claim = {"text": FACT, "evidence_ids": ["fact"]}
        return {"answer": FACT, "status": "answered", "claims": [claim]}
    return {"verdicts": [{"index": 0, "label": "entailed"}]}


@pytest.fixture
def live_service(monkeypatch, tmp_path: Path) -> Iterator[tuple[QueryService, _Gateway]]:
    gateway = _Gateway()
    monkeypatch.setattr(
        LLMProvider, "_post", lambda _self, path, payload, **kw: gateway.post(path, payload, **kw)
    )
    cfg = get_config().model_copy(deep=True)
    cfg.paths.cache_dir = str(tmp_path / "cache")
    settings = Settings.model_construct(llm_api_key="dummy", llm_base_url="https://gateway.test/v1")
    svc = QueryService.create_offline(cfg=cfg, settings=settings)
    svc.allow_llm = True
    svc.enable_cache = False
    svc.bundle.vector.upsert(
        [
            ChunkRecord(
                chunk_id="fact",
                doc_id="doc",
                index=0,
                text=FACT,
                embedding=[0.1] * 8,
                tenant_id="alpha",
            )
        ]
    )
    try:
        yield svc, gateway
    finally:
        svc.close()


def test_executor_and_embeddings_share_the_query_budget(live_service) -> None:
    svc, _gateway = live_service
    budget = BudgetTracker()

    executor, llm = build_query_deps(svc, budget, tenant_id="alpha")

    assert llm.budget is budget
    assert executor.llm is llm
    assert executor.vector.llm is llm


def test_reported_cost_matches_remote_calls(live_service) -> None:
    svc, gateway = live_service

    chain = svc.run_query_chain(
        QueryRequest(question=QUESTION, force_agentic=True), tenant_id="alpha", user_id="u"
    )

    assert gateway.chat > 0 and gateway.embeddings > 0
    assert chain.cost.llm_calls == gateway.chat
    assert chain.cost.embed_calls == gateway.embeddings
    usage = chain.metadata[LLM_BY_ROLE_KEY]
    assert {"planner", "tool_plan", "critic", "composer", "entailment", "embedding"} <= set(usage)
    assert sum(row["calls"] for row in usage.values()) == gateway.chat + gateway.embeddings
    assert "unknown" not in usage


def test_query_budget_cap_includes_tool_planning(live_service) -> None:
    svc, gateway = live_service
    svc.cfg.guardrails.max_llm_calls = 2
    svc.multi_budget.query_limits.max_llm_calls = 2

    # Planner + tool plan spend the budget; the run must not exceed it afterwards.
    with pytest.raises(Exception, match="(?i)budget|max_llm_calls"):
        svc.run_query_chain(
            QueryRequest(question=QUESTION, force_agentic=True), tenant_id="alpha", user_id="u"
        )

    assert gateway.chat == 2


def test_failed_call_releases_its_budget_slot(live_service) -> None:
    _svc, gateway = live_service
    gateway.fail_paths = {"/chat/completions"}
    budget = BudgetTracker()
    llm = LLMProvider(api_key="dummy", budget=budget)

    with pytest.raises(ConnectionError):
        llm.complete(_msgs())

    assert budget.llm_calls == 0


def test_replayed_response_is_charged_to_its_role(live_service, tmp_path: Path) -> None:
    _svc, gateway = live_service
    budget = BudgetTracker()
    llm = LLMProvider(api_key="dummy", budget=budget, cache_dir=tmp_path / "llm")

    with role_scope(LLMRole.PLANNER):
        llm.complete(_msgs())
        llm.complete(_msgs())

    assert gateway.chat == 1
    assert budget.role_usage()["planner"]["calls"] == 2
    assert budget.llm_calls == 2
