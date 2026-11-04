"""One model pool and budget for planning, embedding, verification and writing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.app.knowledge_scope import needs_llm_planning
from agentic_graphrag.app.tenant_context import tenant_entities
from agentic_graphrag.app.wiring import build_executor_for_service, build_llm_for_service
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetTracker

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def query_dependencies(
    svc: QueryService, budget: BudgetTracker, *, tenant_id: str, llm: LLMClient | None = None
) -> tuple[Executor, LLMClient]:
    kb_id = svc.knowledge_base.kb_id if svc.knowledge_base else "default"
    llm = llm or build_llm_for_service(
        allow_llm=svc.allow_llm,
        settings=svc.settings,
        cfg=svc.cfg,
        budget=budget,
        tenant_id=tenant_id,
        kb_id=kb_id,
    )
    executor = build_executor_for_service(
        bundle=svc.bundle,
        cfg=svc.cfg,
        settings=svc.settings,
        allow_llm=svc.allow_llm,
        known_entities=tenant_entities(svc, tenant_id),
        tenant_id=tenant_id,
        kb_id=kb_id,
        model_llm=llm,
        budget=budget,
        retrieval_cache=svc.retrieval_cache,
        enable_cache=svc.enable_cache,
    )
    executor.config.llm_only = needs_llm_planning(svc)
    executor.graph.infer_relations = not executor.config.llm_only
    return executor, llm
