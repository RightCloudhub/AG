"""Retrieval-only use case with the same tenant budget and source audit boundaries."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agentic_graphrag.agent.executor import ToolCallSpec
from agentic_graphrag.agent.executor_dispatch import run_tool_specs
from agentic_graphrag.app.contracts import CostOut
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.evidence_sources import (
    catalog_visible,
    publish_evidence_batch,
    query_scope,
    source_store,
)
from agentic_graphrag.app.knowledge_scope import needs_llm_planning, select_service
from agentic_graphrag.app.query_run import (
    RESERVE_CALLS,
    RESERVE_COST,
    RESERVE_TOKENS,
    _reserve_budget,
)
from agentic_graphrag.app.query_selection import select_documents
from agentic_graphrag.app.search_contracts import SearchCandidate, SearchRequest, SearchResult
from agentic_graphrag.app.tenant_context import tenant_entities
from agentic_graphrag.app.wiring import build_executor_for_service, cost_units_for_chain
from agentic_graphrag.core.enums import RetrievalTool
from agentic_graphrag.core.evidence import Candidate, rrf_fuse
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.observability.degradation import record_degradation

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def search(svc: QueryService, req: SearchRequest, *, tenant_id: str, user_id: str) -> SearchResult:
    selected = select_service(svc, tenant_id=tenant_id, kb_id=req.kb_id)
    selected = select_documents(selected, req.doc_ids, tenant_id=tenant_id)
    _reserve_budget(selected, tenant_id=tenant_id, user_id=user_id)
    budget = BudgetTracker(
        max_llm_calls=selected.cfg.guardrails.max_llm_calls,
        max_tokens=selected.cfg.guardrails.max_tokens_per_query,
    )
    start = time.perf_counter()
    try:
        candidates, tools = _retrieve(selected, req, budget=budget, tenant_id=tenant_id)
        result = SearchResult(
            search_id=str(uuid4()),
            kb_id=req.kb_id,
            candidates=candidates,
            snapshot=selected.snapshot,
            audit_status="failed"
            if any(c.audit_status == "failed" for c in candidates)
            else "persisted",
            cost=CostOut(
                llm_calls=budget.llm_calls,
                tokens=budget.total_tokens,
                prompt_tokens=budget.prompt_tokens,
                completion_tokens=budget.completion_tokens,
                latency_ms=int((time.perf_counter() - start) * 1000),
            ),
            metadata={"tools": tools, "doc_ids": req.doc_ids, "tenant_id": tenant_id},
        )
        return _finalize_search(selected, result, audit_required=req.audit_required)
    finally:
        if selected.multi_budget:
            selected.multi_budget.commit(
                tenant_id=tenant_id,
                user_id=user_id,
                llm_calls=budget.llm_calls,
                tokens=budget.total_tokens,
                cost_units=cost_units_for_chain(budget.llm_calls),
                reserved_calls=RESERVE_CALLS,
                reserved_tokens=RESERVE_TOKENS,
                reserved_cost=RESERVE_COST,
            )


def _finalize_search(
    svc: QueryService, result: SearchResult, *, audit_required: bool
) -> SearchResult:
    withdraw_deleted_sources(svc, result)
    _persist_search(svc, result)
    if audit_required and result.audit_status != "persisted":
        raise ApiError(
            "AUDIT_UNAVAILABLE",
            "Durable search audit could not be recorded",
            status_code=503,
            details={"search_id": result.search_id},
        )
    return result


def withdraw_deleted_sources(svc: QueryService, result: SearchResult) -> None:
    """Recheck original identities, including audits written before citations were retained."""
    for candidate in result.candidates:
        catalog = [candidate.model_dump(mode="json")]
        if "citations" not in candidate.model_fields_set:
            archived = source_store(svc).get(query_scope(svc), candidate.id, kind="evidence")
            if archived is not None:
                catalog.append(archived)
        if not catalog_visible(svc, catalog, current=False):
            candidate.content = ""
            candidate.source_status = "deleted"


def _persist_search(svc: QueryService, result: SearchResult) -> None:
    try:
        source_store(svc).put(
            query_scope(svc), result.model_dump(mode="json"), kind="search", key=result.search_id
        )
    except Exception as exc:
        record_degradation("search_audit", "audit_write_failed", exc)
        result.audit_status = "failed"


def _retrieve(
    svc: QueryService, req: SearchRequest, *, budget: BudgetTracker, tenant_id: str
) -> tuple[list[SearchCandidate], list[str]]:
    executor = build_executor_for_service(
        bundle=svc.bundle,
        cfg=svc.cfg,
        settings=svc.settings,
        allow_llm=svc.allow_llm,
        known_entities=tenant_entities(svc, tenant_id),
        tenant_id=tenant_id,
        kb_id=req.kb_id,
        budget=budget,
        retrieval_cache=None,
        enable_cache=False,
    )
    executor.config.llm_only = needs_llm_planning(svc)
    executor.graph.infer_relations = not executor.config.llm_only
    specs = [
        ToolCallSpec(
            tool=tool,
            args={"query": req.query, "top_k": req.top_k},
            reason="retrieval-only request",
        )
        for tool in (
            RetrievalTool.VECTOR_SEARCH,
            RetrievalTool.FULLTEXT_SEARCH,
            RetrievalTool.GRAPH_SUBGRAPH,
        )
    ]
    groups, traces = run_tool_specs(executor, specs, req.query, tenant_id=tenant_id)
    hits = rrf_fuse(*groups, limit=req.top_k)
    return _candidates(svc, hits), [str(trace.tool) for trace in traces]


def _candidates(svc: QueryService, candidates: list[Candidate]) -> list[SearchCandidate]:
    rows = publish_evidence_batch(
        svc,
        [
            {
                "id": candidate.id,
                "content": candidate.content,
                "citations": [c.model_dump(mode="json") for c in candidate.citations],
                "provenance": candidate.metadata.get("source_refs", []),
            }
            for candidate in candidates
        ],
        scope=query_scope(svc),
    )
    return [_candidate(candidate, row) for candidate, row in zip(candidates, rows, strict=True)]


def _candidate(candidate: Candidate, row: dict[str, Any]) -> SearchCandidate:
    return SearchCandidate(
        id=row["evidence_id"],
        candidate_id=candidate.id,
        content=candidate.content,
        source=candidate.source.value,
        score=candidate.score,
        source_refs=row["source_refs"],
        citations=[citation.model_copy(update={"span": None}) for citation in candidate.citations],
        source_status=row["source_status"],
        audit_status=row["source_audit_status"],
    )
