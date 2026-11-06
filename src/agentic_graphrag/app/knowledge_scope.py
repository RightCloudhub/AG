"""Resolve one authorized knowledge base without mutating process-wide config."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import ValidationError

from agentic_graphrag.app.errors import INVALID_INPUT, ApiError
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.ports.knowledge import KnowledgeStore
from agentic_graphrag.knowledge.review.scoped_queue import ScopedReviewQueue
from agentic_graphrag.knowledge.scoped_tasks import ScopedTaskStore
from agentic_graphrag.stores.knowledge_store import SQLiteKnowledgeStore
from agentic_graphrag.stores.scope_codec import ScopeBinding
from agentic_graphrag.stores.scoped_bundle import scope_bundle

if TYPE_CHECKING:
    from agentic_graphrag.app.contracts import QueryRequest
    from agentic_graphrag.app.query_service import QueryService


def knowledge_store(svc: QueryService) -> KnowledgeStore:
    with svc._lock:
        if svc.knowledge_store is None:
            svc.knowledge_store = SQLiteKnowledgeStore(runtime_dir(svc.cfg) / "knowledge.sqlite3")
        return svc.knowledge_store


def resolve_knowledge_base(svc: QueryService, tenant_id: str, kb_id: str) -> KnowledgeBase:
    try:
        scope = KnowledgeScope(tenant_id=tenant_id or "default", kb_id=kb_id)
    except ValidationError as exc:
        raise ApiError(INVALID_INPUT, "Invalid knowledge scope", status_code=422) from exc
    store = knowledge_store(svc)
    kb = store.ensure_default(scope.tenant_id) if kb_id == "default" else store.get(scope)
    if kb is None:
        raise ApiError(INVALID_INPUT, "Unknown knowledge base", status_code=404)
    return kb


def select_service(
    svc: QueryService, *, tenant_id: str, kb_id: str, generation: str | None = None
) -> QueryService:
    kb = resolve_knowledge_base(svc, tenant_id, kb_id)
    if svc.knowledge_base is not None:
        if svc.knowledge_base.scope != kb.scope:
            raise ApiError(INVALID_INPUT, "Conflicting knowledge scopes", status_code=422)
        if generation is None or generation == svc.knowledge_base.active_generation:
            return svc
        svc = svc.parent or svc
    if generation is not None:
        saved = knowledge_store(svc).get_snapshot(kb.scope, generation)
        kb = (saved[0].knowledge_base if saved else None) or kb
        kb = kb.model_copy(update={"active_generation": generation})
    return _bind_service(svc, kb)


def _bind_service(svc: QueryService, kb: KnowledgeBase) -> QueryService:
    from agentic_graphrag.app.model_config import effective_model_config

    cfg = effective_model_config(
        svc.cfg,
        svc.settings,
        tenant_id=kb.tenant_id,
        kb_id=kb.kb_id,
        profile_id=kb.model_profile_id,
        profile_revision=kb.model_profile_revision,
    )
    if kb.build_mode == "open" or kb.rule_planner_profile != "legacy-company":
        cfg.guardrails.requirement_register.enabled = True
    bundle = scope_bundle(svc.bundle, kb.scope, generation=kb.active_generation)
    binding = ScopeBinding(kb.scope, kb.active_generation)
    selected = replace(
        svc,
        cfg=cfg,
        bundle=bundle,
        knowledge_base=kb,
        parent=svc,
        known_entities=[],
        review_executor=None,
        ingest_tasks=ScopedTaskStore(svc.ingest_tasks, binding) if svc.ingest_tasks else None,
        review_queue=ScopedReviewQueue(svc.review_queue, binding) if svc.review_queue else None,
        # A cache may only be reused when all its scope/version inputs match.
        enable_cache=svc.enable_cache and kb.kb_id == "default" and kb.config_revision == 1,
    )
    if svc.review_executor is not None:
        from agentic_graphrag.app.write_path import attach_write_path

        attach_write_path(selected)
    return selected


def prepare_query_service(svc: QueryService, req: QueryRequest, *, tenant_id: str) -> QueryService:
    from agentic_graphrag.app.global_query import uses_global
    from agentic_graphrag.app.query_selection import select_documents

    selected = select_service(svc, tenant_id=tenant_id, kb_id=req.kb_id)
    uses_global(selected, req)
    if needs_llm_planning(selected) and not selected.allow_llm:
        raise ApiError(
            "CAPABILITY_UNAVAILABLE",
            "This knowledge base requires enabled LLM capabilities",
            status_code=503,
        )
    return select_documents(selected, req.doc_ids, tenant_id=tenant_id)


def needs_llm_planning(svc: QueryService) -> bool:
    kb = svc.knowledge_base
    return bool(kb and (kb.build_mode == "open" or kb.rule_planner_profile != "legacy-company"))
