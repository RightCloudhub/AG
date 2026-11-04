"""Recover summary work from published generations, including missed process events."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, Field

from agentic_graphrag.app.knowledge_scope import knowledge_store
from agentic_graphrag.app.query_selection import select_documents
from agentic_graphrag.app.summary_index import build_summary_index, enqueue_artifacts
from agentic_graphrag.app.summary_prepare import summary_artifacts, summary_service
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.knowledge import SummaryRecord
from agentic_graphrag.core.ports.communities import CommunityDetector
from agentic_graphrag.core.ports.knowledge import SummaryStore
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.observability.degradation import record_degradation
from agentic_graphrag.stores.summary_jobs import SummaryJobStore

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

SCAN_LIMIT = 100000


class SummaryIndexStatus(BaseModel):
    kb_id: str
    generation: str
    enabled: bool
    document_count: int
    ready_documents: int
    partial_documents: int
    community_reports: int
    status: str
    jobs: dict[str, int] = Field(default_factory=dict)


def summary_status(svc: QueryService) -> SummaryIndexStatus:
    kb = svc.knowledge_base
    assert kb is not None
    reports, count = _visible_reports(svc)
    counts = _job_counts(svc)
    ready = sum(row.kind == "document" and row.status == "ready" for row in reports)
    partial = sum(row.kind == "document" and row.status == "partial" for row in reports)
    status = _readiness(
        enabled=kb.global_summary_enabled,
        counts=counts,
        complete=ready == count,
        any_ready=bool(ready),
        partial=any(row.status == "partial" for row in reports),
    )
    return SummaryIndexStatus(
        kb_id=kb.kb_id,
        generation=kb.active_generation,
        enabled=kb.global_summary_enabled,
        document_count=count,
        ready_documents=ready,
        partial_documents=partial,
        community_reports=sum(row.kind == "community" for row in reports),
        status=status,
        jobs=counts,
    )


def _visible_reports(svc: QueryService) -> tuple[list[SummaryRecord], int]:
    kb = svc.knowledge_base
    assert kb is not None
    selected = select_documents(svc, None, tenant_id=kb.tenant_id)
    visible = set(selected.bundle.docs.list_ids(tenant_id=kb.tenant_id))
    reports = cast(SummaryStore, knowledge_store(svc)).summaries(
        kb.scope, generation=kb.active_generation
    )
    reports = [
        row
        for row in reports
        if set(row.document_ids) <= visible
        and all(source.doc_id in visible for finding in row.findings for source in finding.sources)
    ]
    return reports, len(visible)


def _job_counts(svc: QueryService) -> dict[str, int]:
    kb = svc.knowledge_base
    assert kb is not None
    store = SummaryJobStore(runtime_dir(svc.cfg) / "knowledge.sqlite3")
    try:
        rows = store.jobs(kb.scope, generation=kb.active_generation, limit=SCAN_LIMIT)
        return dict(Counter(str(row.status) for row in rows))
    finally:
        store.close()


def _readiness(
    *, enabled: bool, counts: dict[str, int], complete: bool, any_ready: bool, partial: bool
) -> str:
    if not enabled:
        return "disabled"
    if counts.get("failed"):
        return "partial" if any_ready or partial else "failed"
    if partial:
        return "partial"
    if complete and not (counts.get("pending") or counts.get("running")):
        return "ready"
    return "pending"


def queue_summary_index(
    svc: QueryService, *, detector: CommunityDetector | None = None
) -> SummaryIndexStatus:
    selected = summary_service(svc, has_client=False)
    artifacts, _ = summary_artifacts(selected, detector=detector)
    jobs = SummaryJobStore(runtime_dir(svc.cfg) / "knowledge.sqlite3")
    try:
        enqueue_artifacts(jobs, artifacts)
    finally:
        jobs.close()
    return summary_status(selected)


def process_summary_work(
    svc: QueryService, *, llm: LLMClient | None = None, detector: CommunityDetector | None = None
) -> None:
    if not (svc.allow_llm or llm is not None):
        return
    metadata = knowledge_store(svc)
    jobs = SummaryJobStore(runtime_dir(svc.cfg) / "knowledge.sqlite3")
    try:
        for scope in metadata.scopes():
            kb = metadata.get(scope)
            if kb is None or not kb.global_summary_enabled:
                continue
            rows = jobs.jobs(scope, generation=kb.active_generation, limit=SCAN_LIMIT)
            if rows and not any(row.status in {"pending", "running"} for row in rows):
                continue
            _process_one(svc, kb.kb_id, tenant_id=kb.tenant_id, llm=llm, detector=detector)
    finally:
        jobs.close()


def _process_one(
    svc: QueryService,
    kb_id: str,
    *,
    tenant_id: str,
    llm: LLMClient | None,
    detector: CommunityDetector | None,
) -> None:
    try:
        build_summary_index(svc, tenant_id=tenant_id, kb_id=kb_id, llm=llm, detector=detector)
    except Exception as exc:
        record_degradation("summary_index", "summary_index_failed", exc, kb_id=kb_id)
