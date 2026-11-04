"""Durable summary worker leases, stale-result fencing and atomic publication."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef, SummaryFinding, SummaryRecord
from agentic_graphrag.stores.knowledge_store import SQLiteKnowledgeStore
from agentic_graphrag.stores.summary_jobs import SummaryJobStore


def enqueue(store, *, scope=None, fingerprint="input", generation="1", attempts=3):
    return store.enqueue(
        scope or KnowledgeScope(),
        generation=generation,
        kind="document",
        artifact_id="doc-summary",
        input_fingerprint=fingerprint,
        model_revision="writer-1",
        prompt_version="prompt-1",
        max_attempts=attempts,
    )


def summary(job, *, source=False):
    findings = ()
    if source:
        ref = SourceRef(
            scope=job.scope, doc_id="doc", doc_version=1, chunk_id="chunk", content_sha256="a" * 64
        )
        findings = (SummaryFinding(text="A supported risk.", sources=(ref,)),)
    return SummaryRecord(
        summary_id=job.artifact_id,
        scope=job.scope,
        generation=job.generation,
        kind=job.kind,
        findings=findings,
        input_fingerprint=job.input_fingerprint,
        model_revision=job.model_revision,
        prompt_version=job.prompt_version,
        status="ready",
    )


def test_jobs_and_summary_publication_survive_reopen_and_duplicate_delivery(tmp_path):
    path = tmp_path / "metadata.db"
    queue = SummaryJobStore(path)
    job = enqueue(queue)
    assert enqueue(queue) == job
    queue.close()
    reopened = SummaryJobStore(path)
    claimed = reopened.claim("worker")
    assert claimed.job_id == job.job_id and claimed.attempts == 1
    result = summary(claimed)
    finished = reopened.complete(claimed, result)
    assert reopened.complete(claimed, result) == finished
    metadata = SQLiteKnowledgeStore(path)
    assert metadata.summaries(job.scope, generation="1") == [result]
    assert reopened.claim("worker") is None
    assert enqueue(reopened).status == "completed"
    with pytest.raises(ValueError, match="new generation"):
        enqueue(reopened, fingerprint="changed")
    assert enqueue(reopened, generation="2").status == "pending"
    reopened.close()
    metadata.close()


def test_two_workers_claim_once_and_expired_worker_cannot_complete(tmp_path):
    now = [100.0]
    path = tmp_path / "metadata.db"
    first, second = (
        SummaryJobStore(path, clock=lambda: now[0]),
        SummaryJobStore(path, clock=lambda: now[0]),
    )
    job = enqueue(first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: pair[0].claim(pair[1], lease_seconds=10),
                [(first, "one"), (second, "two")],
            )
        )
    claimed = next(row for row in results if row is not None)
    assert sum(row is not None for row in results) == 1
    now[0] = 111.0
    recovered = second.claim("recovery", lease_seconds=10)
    assert recovered.job_id == job.job_id and recovered.attempts == 2
    assert recovered.lease_token != claimed.lease_token
    with pytest.raises(ValueError, match="lease"):
        first.complete(claimed, summary(claimed))
    assert second.complete(recovered, summary(recovered)).status == "completed"


def test_superseded_fingerprint_rejects_old_results_and_cannot_overwrite_summary(tmp_path):
    path = tmp_path / "metadata.db"
    queue = SummaryJobStore(path)
    old = enqueue(queue)
    old_claim = queue.claim("slow")
    current = enqueue(queue, fingerprint="new-input")
    assert queue.get_job(KnowledgeScope(), old.job_id).status == "superseded"
    with pytest.raises(ValueError, match="lease"):
        queue.complete(old_claim, summary(old_claim))
    claimed = queue.claim("fast")
    assert claimed.job_id == current.job_id
    queue.complete(claimed, summary(claimed))
    metadata = SQLiteKnowledgeStore(path)
    assert metadata.summaries(KnowledgeScope(), generation="1")[0].input_fingerprint == "new-input"


def test_retry_budget_heartbeat_and_final_expired_lease_are_visible(tmp_path):
    now = [100.0]
    queue = SummaryJobStore(tmp_path / "metadata.db", clock=lambda: now[0])
    job = enqueue(queue, attempts=2)
    first = queue.claim("worker", lease_seconds=10)
    now[0] = 105.0
    renewed = queue.heartbeat(first, lease_seconds=10)
    assert renewed.lease_until == 115.0
    assert queue.fail(renewed, error_code="model_timeout").status == "pending"
    final = queue.claim("worker", lease_seconds=10)
    now[0] = 116.0
    assert queue.claim("replacement") is None
    assert queue.get_job(job.scope, job.job_id).status == "failed"
    assert queue.get_job(job.scope, job.job_id).error_code == "lease_expired"
    with pytest.raises(ValueError, match="lease"):
        queue.heartbeat(final)


def test_scopes_versions_and_source_tombstones_are_enforced(tmp_path):
    path = tmp_path / "metadata.db"
    queue = SummaryJobStore(path)
    scope = KnowledgeScope(tenant_id="a", kb_id="contracts")
    job = enqueue(queue, scope=scope)
    assert queue.get_job(KnowledgeScope(tenant_id="b", kb_id="contracts"), job.job_id) is None
    assert queue.claim("other", scope=KnowledgeScope()) is None
    claimed = queue.claim("worker", scope=scope)
    with pytest.raises(ValueError, match="does not match"):
        queue.complete(claimed, summary(claimed).model_copy(update={"generation": "wrong"}))
    metadata = SQLiteKnowledgeStore(path)
    metadata.tombstone(scope, "doc", through_version=1)
    with pytest.raises(ValueError, match="withdrawn"):
        queue.complete(claimed, summary(claimed, source=True))
    assert metadata.summaries(scope, generation="1") == []
    assert queue.get_job(scope, job.job_id).status == "running"
    assert len(queue.jobs(scope, generation="1")) == 1
    assert not queue.jobs(KnowledgeScope())


def test_ready_summary_is_immutable_and_jobs_fail_explicitly(tmp_path):
    path = tmp_path / "metadata.db"
    queue = SummaryJobStore(path)
    enqueue(queue)
    job = queue.claim("worker")
    assert queue.fail(job, error_code="invalid_sources", retry=False).status == "failed"
    metadata = SQLiteKnowledgeStore(path)
    ready = summary(job)
    metadata.save_summary(ready)
    with pytest.raises(ValueError, match="immutable"):
        metadata.save_summary(ready.model_copy(update={"status": "pending"}))
    with pytest.raises(ValueError, match="model"):
        metadata.save_summary(ready.model_copy(update={"model_revision": "other"}))


def test_claim_selects_only_requested_generation(tmp_path):
    store = SummaryJobStore(tmp_path / "metadata.db")
    older = enqueue(store, generation="old")
    newer = enqueue(store, generation="new")
    claimed = store.claim("worker", scope=KnowledgeScope(), generation="new")
    assert claimed.job_id == newer.job_id
    assert store.get_job(older.scope, older.job_id).status == "pending"
    assert store.claim("worker", scope=KnowledgeScope(), generation="new") is None
