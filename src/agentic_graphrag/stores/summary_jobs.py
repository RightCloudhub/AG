"""At-least-once summary jobs with immutable inputs and fenced worker completion."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import uuid4

from agentic_graphrag.core.knowledge import KnowledgeScope, SummaryRecord
from agentic_graphrag.core.summary_jobs import SummaryJob
from agentic_graphrag.stores.knowledge_summaries import save_summary_record
from agentic_graphrag.stores.summary_job_database import (
    SummaryJobDatabase,
    expire_exhausted,
    job_key,
    leased_job,
    read_job,
    validate_summary,
    write_job,
)

MAX_LEASE_SECONDS = 600


class SummaryJobStore(SummaryJobDatabase):
    def enqueue(
        self,
        scope: KnowledgeScope,
        *,
        generation: str,
        kind: Literal["document", "community"],
        artifact_id: str,
        input_fingerprint: str,
        model_revision: str,
        prompt_version: str,
        max_attempts: int = 3,
    ) -> SummaryJob:
        identity = [
            scope.model_dump(),
            generation,
            kind,
            artifact_id,
            input_fingerprint,
            model_revision,
            prompt_version,
        ]
        job_id = (
            "summary-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        )
        job = SummaryJob(
            job_id=job_id,
            scope=scope,
            generation=generation,
            kind=kind,
            artifact_id=artifact_id,
            input_fingerprint=input_fingerprint,
            model_revision=model_revision,
            prompt_version=prompt_version,
            max_attempts=max_attempts,
        )
        return self._enqueue(job)

    def _enqueue(self, job: SummaryJob) -> SummaryJob:
        job_id = job.job_id
        with self.transaction() as db:
            existing = read_job(db, job_id)
            if existing is not None:
                return existing
            head = db.execute(
                "SELECT job_id FROM summary_job_heads "
                "WHERE tenant=? AND kb=? AND generation=? AND kind=? AND artifact=?",
                job_key(job),
            ).fetchone()
            if head:
                old = read_job(db, head[0])
                if old and old.status == "completed":
                    raise ValueError("Completed summary inputs require a new generation")
                if old:
                    write_job(
                        db, old.model_copy(update={"status": "superseded", "lease_token": ""})
                    )
            write_job(db, job)
            db.execute(
                "INSERT OR REPLACE INTO summary_job_heads VALUES (?,?,?,?,?,?)",
                (*job_key(job), job_id),
            )
        return job

    def claim(
        self,
        owner: str,
        *,
        lease_seconds: float = 120,
        scope: KnowledgeScope | None = None,
        generation: str | None = None,
    ) -> SummaryJob | None:
        _validate_lease(owner, lease_seconds)
        now = self.clock()
        with self.transaction() as db:
            expire_exhausted(db, now=now, scope=scope)
            sql = (
                "SELECT payload FROM summary_jobs WHERE attempts<max_attempts "
                "AND (status='pending' OR (status='running' AND lease_until<=?))"
            )
            params: list[str | float] = [now]
            if scope is not None:
                sql += " AND tenant=? AND kb=?"
                params.extend([scope.tenant_id, scope.kb_id])
            if generation is not None:
                sql += " AND generation=?"
                params.append(generation)
            row = db.execute(sql + " ORDER BY rowid LIMIT 1", params).fetchone()
            if not row:
                return None
            old = SummaryJob.model_validate_json(row[0])
            claimed = old.model_copy(
                update={
                    "status": "running",
                    "attempts": old.attempts + 1,
                    "owner": owner,
                    "lease_token": uuid4().hex,
                    "lease_until": now + lease_seconds,
                }
            )
            write_job(db, claimed)
            return claimed

    def heartbeat(self, job: SummaryJob, *, lease_seconds: float = 120) -> SummaryJob:
        _validate_lease(job.owner, lease_seconds)
        with self.transaction() as db:
            stored = leased_job(db, job, now=self.clock())
            renewed = stored.model_copy(update={"lease_until": self.clock() + lease_seconds})
            write_job(db, renewed)
            return renewed

    def complete(self, job: SummaryJob, summary: SummaryRecord) -> SummaryJob:
        with self.transaction() as db:
            previous = read_job(db, job.job_id)
            if (
                previous
                and previous.status == "completed"
                and previous.scope == job.scope
                and previous.lease_token == job.lease_token
            ):
                validate_summary(db, previous, summary)
                save_summary_record(db, summary)
                return previous
            stored = leased_job(db, job, now=self.clock())
            validate_summary(db, stored, summary)
            save_summary_record(db, summary)
            completed = stored.model_copy(update={"status": "completed", "lease_until": 0.0})
            write_job(db, completed)
            return completed

    def fail(self, job: SummaryJob, *, error_code: str, retry: bool = True) -> SummaryJob:
        if not error_code or len(error_code) > 200:
            raise ValueError("A bounded error code is required")
        with self.transaction() as db:
            stored = leased_job(db, job, now=self.clock())
            retryable = retry and stored.attempts < stored.max_attempts
            failed = stored.model_copy(
                update={
                    "status": "pending" if retryable else "failed",
                    "lease_token": "",
                    "lease_until": 0.0,
                    "error_code": error_code,
                }
            )
            write_job(db, failed)
            return failed

    def retry(self, scope: KnowledgeScope, job_id: str) -> SummaryJob:
        with self.transaction() as db:
            job = read_job(db, job_id)
            if job is None or job.scope != scope:
                raise KeyError("Unknown summary job")
            if job.status != "failed":
                raise ValueError(
                    "Only failed jobs can retry; partial reports need a new generation"
                )
            pending = job.model_copy(
                update={
                    "status": "pending",
                    "attempts": 0,
                    "error_code": "",
                    "owner": "",
                    "lease_token": "",
                    "lease_until": 0.0,
                }
            )
            write_job(db, pending)
            return pending


def _validate_lease(owner: str, seconds: float) -> None:
    if not owner or not 0 < seconds <= MAX_LEASE_SECONDS:
        raise ValueError("Worker owner and a lease in (0,600] seconds are required")
