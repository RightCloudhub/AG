"""SQL helpers for leased summary jobs; local-volume SQLite only."""

import sqlite3
from collections.abc import Callable
from pathlib import Path
from time import time

from agentic_graphrag.core.knowledge import KnowledgeScope, SummaryRecord
from agentic_graphrag.core.summary_jobs import SummaryJob
from agentic_graphrag.stores.knowledge_database import KnowledgeDatabase, scope_params

_SCHEMA = """
CREATE TABLE IF NOT EXISTS summary_jobs (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kb TEXT NOT NULL, generation TEXT NOT NULL,
    kind TEXT NOT NULL, artifact TEXT NOT NULL, status TEXT NOT NULL,
    lease_until REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS summary_job_ready ON summary_jobs(status,lease_until);
CREATE TABLE IF NOT EXISTS summary_job_heads (
    tenant TEXT NOT NULL, kb TEXT NOT NULL, generation TEXT NOT NULL, kind TEXT NOT NULL,
    artifact TEXT NOT NULL, job_id TEXT NOT NULL, PRIMARY KEY(tenant,kb,generation,kind,artifact));
"""


def job_key(job: SummaryJob) -> tuple[str, str, str, str, str]:
    return (*scope_params(job.scope), job.generation, job.kind, job.artifact_id)


def read_job(db: sqlite3.Connection, job_id: str) -> SummaryJob | None:
    row = db.execute("SELECT payload FROM summary_jobs WHERE id=?", (job_id,)).fetchone()
    return SummaryJob.model_validate_json(row[0]) if row else None


def write_job(db: sqlite3.Connection, job: SummaryJob) -> None:
    db.execute(
        "INSERT OR REPLACE INTO summary_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            job.job_id,
            *job_key(job),
            job.status,
            job.lease_until,
            job.attempts,
            job.max_attempts,
            job.model_dump_json(),
        ),
    )


def expire_exhausted(db: sqlite3.Connection, *, now: float, scope: KnowledgeScope | None) -> None:
    query = (
        "SELECT payload FROM summary_jobs WHERE status='running' AND lease_until<=? "
        "AND attempts>=max_attempts"
    )
    params: list[str | float] = [now]
    if scope is not None:
        query += " AND tenant=? AND kb=?"
        params.extend(scope_params(scope))
    rows = db.execute(query, params).fetchall()
    for row in rows:
        job = SummaryJob.model_validate_json(row[0])
        write_job(
            db,
            job.model_copy(
                update={
                    "status": "failed",
                    "error_code": "lease_expired",
                    "lease_token": "",
                }
            ),
        )


def leased_job(db: sqlite3.Connection, job: SummaryJob, *, now: float) -> SummaryJob:
    stored = read_job(db, job.job_id)
    if stored is None or stored.scope != job.scope:
        raise ValueError("Summary job does not exist in the requested scope")
    if (
        stored.status != "running"
        or stored.lease_token != job.lease_token
        or stored.lease_until <= now
    ):
        raise ValueError("Summary job lease was lost")
    head = db.execute(
        "SELECT job_id FROM summary_job_heads "
        "WHERE tenant=? AND kb=? AND generation=? AND kind=? AND artifact=?",
        job_key(job),
    ).fetchone()
    if not head or head[0] != job.job_id:
        raise ValueError("Summary job was superseded by another input fingerprint")
    return stored


def validate_summary(db: sqlite3.Connection, job: SummaryJob, summary: SummaryRecord) -> None:
    if (
        summary.scope,
        summary.generation,
        summary.kind,
        summary.summary_id,
        summary.input_fingerprint,
        summary.model_revision,
        summary.prompt_version,
    ) != (
        job.scope,
        job.generation,
        job.kind,
        job.artifact_id,
        job.input_fingerprint,
        job.model_revision,
        job.prompt_version,
    ):
        raise ValueError("Summary does not match the claimed job")
    if summary.status not in {"ready", "partial"}:
        raise ValueError("A completed summary job needs a ready or partial result")
    for finding in summary.findings:
        for source in finding.sources:
            row = db.execute(
                "SELECT through_version FROM source_tombstones "
                "WHERE tenant=? AND kb=? AND doc_id=?",
                (*scope_params(summary.scope), source.doc_id),
            ).fetchone()
            if row and source.doc_version <= row[0]:
                raise ValueError("Summary references a withdrawn document version")


class SummaryJobDatabase(KnowledgeDatabase):
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time) -> None:
        super().__init__(path)
        self.clock = clock
        with self._mutex:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def get_job(self, scope: KnowledgeScope, job_id: str) -> SummaryJob | None:
        with self.transaction() as db:
            job = read_job(db, job_id)
        return job if job is not None and job.scope == scope else None

    def jobs(
        self,
        scope: KnowledgeScope,
        *,
        generation: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SummaryJob]:
        query = "SELECT payload FROM summary_jobs WHERE tenant=? AND kb=?"
        params: list[str | int] = list(scope_params(scope))
        if generation is not None:
            query += " AND generation=?"
            params.append(generation)
        params.extend([max(0, limit), max(0, offset)])
        with self.transaction() as db:
            rows = db.execute(query + " ORDER BY rowid LIMIT ? OFFSET ?", params).fetchall()
        return [SummaryJob.model_validate_json(row[0]) for row in rows]
