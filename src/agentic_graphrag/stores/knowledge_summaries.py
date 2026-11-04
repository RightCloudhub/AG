"""Summary publication rules shared by direct writes and durable worker completion."""

import sqlite3

from agentic_graphrag.core.knowledge import SummaryRecord
from agentic_graphrag.stores.knowledge_database import scope_params


def save_summary_record(db: sqlite3.Connection, summary: SummaryRecord) -> None:
    key = (*scope_params(summary.scope), summary.generation, summary.summary_id)
    existing = db.execute(
        "SELECT payload FROM knowledge_summaries WHERE tenant=? AND kb=? AND generation=? AND id=?",
        key,
    ).fetchone()
    if existing:
        old = SummaryRecord.model_validate_json(existing[0])
        identity = (old.input_fingerprint, old.model_revision, old.prompt_version)
        if identity != (summary.input_fingerprint, summary.model_revision, summary.prompt_version):
            raise ValueError("A summary generation cannot change its input fingerprint or model")
        if old.status == "ready" and old != summary:
            raise ValueError("A completed summary is immutable within its generation")
    db.execute(
        "INSERT OR REPLACE INTO knowledge_summaries VALUES (?,?,?,?,?,?)",
        (*key, summary.input_fingerprint, summary.model_dump_json()),
    )
