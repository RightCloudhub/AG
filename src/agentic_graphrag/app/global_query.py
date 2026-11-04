"""Global strategy selection and the common synchronous/streaming query adapter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, cast

from agentic_graphrag.agent.global_intent import is_global_question
from agentic_graphrag.agent.global_query import GlobalContext, global_query
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.core.ports.knowledge import SummaryStore
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetTracker

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def uses_global(svc: QueryService, req: QueryRequest) -> bool:
    if req.force_agentic or req.strategy == "local":
        return False
    enabled = bool(svc.knowledge_base and svc.knowledge_base.global_summary_enabled)
    selected = req.strategy == "global" or (enabled and is_global_question(req.question))
    if selected and (not enabled or not svc.allow_llm):
        raise ApiError(
            "CAPABILITY_UNAVAILABLE",
            "Global queries require enabled summaries and models",
            status_code=503,
        )
    return selected


def configure_global_budget(svc: QueryService, req: QueryRequest, budget: BudgetTracker) -> None:
    if uses_global(svc, req):
        budget.max_llm_calls = svc.cfg.summaries.query_max_llm_calls
        budget.max_tokens = svc.cfg.summaries.query_max_tokens


def global_progress(
    svc: QueryService, req: QueryRequest, *, llm: LLMClient, budget: BudgetTracker, query_id: str
) -> Iterator[tuple[str, Any]]:
    kb = svc.knowledge_base
    assert kb is not None and svc.knowledge_store is not None
    reports = cast(SummaryStore, svc.knowledge_store).summaries(
        kb.scope, generation=kb.active_generation
    )
    yield from global_query(
        req.question,
        GlobalContext(
            scope=kb.scope,
            docs=svc.bundle.docs,
            summaries=reports,
            llm=llm,
            budget=budget,
            config=svc.cfg.summaries,
            query_id=query_id,
        ),
    )


def run_global_query(
    svc: QueryService, req: QueryRequest, *, llm: LLMClient, budget: BudgetTracker, query_id: str
) -> ReasoningChain:
    for event, payload in global_progress(svc, req, llm=llm, budget=budget, query_id=query_id):
        if event == "__final_chain__":
            return cast(ReasoningChain, payload)
    raise RuntimeError("Global query ended without a result")
