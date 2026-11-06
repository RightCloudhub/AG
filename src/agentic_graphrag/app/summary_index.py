"""Background summary jobs share publication scope, source versions and model budgets."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from agentic_graphrag.app.knowledge_scope import select_service
from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.knowledge import SummaryRecord
from agentic_graphrag.core.ports.communities import CommunityDetector
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.summarizer import summarize
from agentic_graphrag.knowledge.summary_artifacts import (
    SummaryArtifact,
    reusable,
)
from agentic_graphrag.observability.degradation import record_degradation
from agentic_graphrag.stores.summary_jobs import SummaryJobStore

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def build_summary_index(
    svc: QueryService,
    *,
    tenant_id: str,
    kb_id: str,
    llm: LLMClient | None = None,
    detector: CommunityDetector | None = None,
) -> dict[str, int]:
    from agentic_graphrag.app.summary_prepare import summary_artifacts, summary_service

    selected = summary_service(
        select_service(svc, tenant_id=tenant_id, kb_id=kb_id), has_client=llm is not None
    )
    artifacts, prior = summary_artifacts(selected, detector=detector)
    client = llm or selected._build_llm(
        BudgetTracker(
            max_llm_calls=svc.cfg.summaries.index_max_llm_calls,
            max_tokens=svc.cfg.summaries.index_max_tokens,
        )
    )
    jobs = SummaryJobStore(runtime_dir(svc.cfg) / "knowledge.sqlite3")
    try:
        return _run_jobs(selected, artifacts, jobs=jobs, prior=prior, llm=client)
    finally:
        jobs.close()
        if llm is None and (close := getattr(client, "close", None)) is not None:
            close()


def _run_jobs(
    svc: QueryService,
    artifacts: list[SummaryArtifact],
    *,
    jobs: SummaryJobStore,
    prior: list[SummaryRecord],
    llm: LLMClient,
) -> dict[str, int]:
    kb = svc.knowledge_base
    assert kb is not None
    by_id = {artifact.record.summary_id: artifact for artifact in artifacts}
    previous = {record.summary_id: record for record in prior}
    counts = {"generated": 0, "reused": 0, "failed": 0}
    enqueue_artifacts(jobs, artifacts)
    owner = str(uuid4())
    while job := jobs.claim(
        owner, scope=kb.scope, generation=kb.active_generation, lease_seconds=600
    ):
        try:
            artifact = by_id[job.artifact_id]
            old = previous.get(job.artifact_id)
            if old is not None and reusable(old, artifact.record):
                result = old.model_copy(update={"generation": kb.active_generation})
                counts["reused"] += 1
            else:
                result = summarize(
                    artifact.record,
                    artifact.inputs,
                    llm=llm,
                    config=svc.cfg.summaries,
                    context=artifact.context,
                )
                counts["generated"] += 1
            jobs.complete(job, result)
        except Exception as exc:
            record_degradation("summary_job", "summary_job_failed", exc)
            jobs.fail(job, error_code=type(exc).__name__, retry=False)
            counts["failed"] += 1
    return counts


def enqueue_artifacts(jobs: SummaryJobStore, artifacts: list[SummaryArtifact]) -> None:
    for artifact in artifacts:
        row = artifact.record
        jobs.enqueue(
            row.scope,
            generation=row.generation,
            kind=row.kind,
            artifact_id=row.summary_id,
            input_fingerprint=row.input_fingerprint,
            model_revision=row.model_revision,
            prompt_version=row.prompt_version,
        )
