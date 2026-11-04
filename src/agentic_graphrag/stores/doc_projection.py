"""Read-model projections over FileDocStore SQLite (K-01).

Lists aggregate chunk projection columns and never decode document or chunk
payloads (which hold full text and embeddings). Sorting is deterministic.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agentic_graphrag.core.ports.document_ops import DocumentQuery
from agentic_graphrag.core.ports.stores import INDEX_STAGES
from agentic_graphrag.stores.doc_ops_sql import tenant_key

DOCUMENT_STATUSES = (
    "parsing",
    "parse_failed",
    "pending",
    "ready",
    "failed",
    "deleting",
    "replacing",
)
_STAGE_FLAG = {"vector": "embedded", "fulltext": "fulltext_indexed", "graph": "graph_extracted"}


def _stage_aggregates() -> str:
    parts = []
    for stage, flag in _STAGE_FLAG.items():
        parts.append(f"SUM(CASE WHEN enabled=1 AND {flag}=1 THEN 1 ELSE 0 END) AS {stage}_n")
        parts.append(f"MAX(json_extract(summary,'$.stages.{stage}.generation')) AS {stage}_gen")
        parts.append(f"MAX(json_extract(summary,'$.stages.{stage}.error')) AS {stage}_err")
    return ", ".join(parts)


_AGG = (
    "SELECT doc_id, COUNT(*) AS total, SUM(enabled) AS enabled_n, "
    "MAX(CASE WHEN enabled=1 THEN model END) AS model, "
    f"{_stage_aggregates()} FROM chunks WHERE tenant=:tenant GROUP BY doc_id"
)
_ACTIVE = (
    "EXISTS(SELECT 1 FROM ops o WHERE o.tenant=d.tenant AND o.doc_id=d.id "
    "AND o.state IN ('pending','running'))"
)
_STATUS = (
    "CASE WHEN d.lifecycle!='' THEN d.lifecycle "
    f"WHEN d.index_complete=1 AND NOT {_ACTIVE} THEN 'ready' "
    "WHEN d.index_error!='' AND d.index_complete=0 THEN 'failed' ELSE 'pending' END"
)
_DOCS = (
    "SELECT d.id AS doc_id, d.title, d.source, d.version, d.created_at, d.updated_at, "
    "d.lifecycle, d.index_error, d.task_id, 0 AS reserved, "
    f"{_STATUS} AS status FROM documents d WHERE d.tenant=:tenant"
)
_ISO = "strftime('%Y-%m-%dT%H:%M:%f+00:00', {0}, 'unixepoch')"
_RESERVED = (
    "SELECT r.doc_id, r.title, r.source, 0, "
    f"{_ISO.format('r.created_at')}, {_ISO.format('r.updated_at')}, '', r.error, r.op_id, 1, "
    "CASE WHEN r.state='failed' THEN 'parse_failed' ELSE 'parsing' END "
    "FROM reservations r WHERE r.tenant=:tenant AND r.state IN ('queued','running','failed') "
    "AND NOT EXISTS(SELECT 1 FROM documents x WHERE x.tenant=r.tenant AND x.id=r.doc_id)"
)
_BASE = f"SELECT * FROM ({_DOCS} UNION ALL {_RESERVED}) AS u"
_AGG_FIELDS = ", ".join(
    ["a.total", "a.enabled_n", "a.model"]
    + [f"a.{stage}_{part}" for stage in _STAGE_FLAG for part in ("n", "gen", "err")]
)


def _filters(query: DocumentQuery) -> tuple[str, dict[str, Any]]:
    clauses, params = [], {"tenant": tenant_key(query.tenant_id)}
    if query.query:
        escaped = query.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["like"] = f"%{escaped}%"
        clauses.append("(u.title LIKE :like ESCAPE '\\' OR u.source LIKE :like ESCAPE '\\')")
    if query.status:
        params["status"] = query.status
        clauses.append("u.status=:status")
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def list_documents(
    db: sqlite3.Connection, query: DocumentQuery
) -> tuple[list[dict[str, Any]], int]:
    where, params = _filters(query)
    total = int(db.execute(f"SELECT COUNT(*) FROM ({_BASE}{where})", params).fetchone()[0])
    sql = (
        f"SELECT u.*, {_AGG_FIELDS} FROM ({_BASE}{where}) AS u LEFT JOIN ({_AGG}) AS a "
        "ON a.doc_id=u.doc_id ORDER BY u.updated_at DESC, u.doc_id ASC LIMIT :limit OFFSET :offset"
    )
    params.update(limit=query.limit, offset=query.offset)
    return [_row(row) for row in _dict_rows(db, sql, params)], total


def document_row(db: sqlite3.Connection, tenant: str, doc_id: str) -> dict[str, Any] | None:
    sql = (
        f"SELECT u.*, {_AGG_FIELDS} FROM ({_BASE} WHERE u.doc_id=:doc) AS u "
        f"LEFT JOIN ({_AGG}) AS a ON a.doc_id=u.doc_id "
        "ORDER BY u.reserved ASC, u.updated_at DESC LIMIT 1"
    )
    rows = _dict_rows(db, sql, {"tenant": tenant_key(tenant), "doc": doc_id})
    return _row(rows[0]) if rows else None


def _dict_rows(db: sqlite3.Connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cursor = db.execute(sql, params)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _stage_summary(row: dict[str, Any], stage: str) -> dict[str, Any]:
    return {
        "indexed": int(row.get(f"{stage}_n") or 0),
        "generation": int(row.get(f"{stage}_gen") or 0),
        "error": row.get(f"{stage}_err"),
    }


_TEXT_FIELDS = (
    "title",
    "source",
    "created_at",
    "updated_at",
    "lifecycle",
    "index_error",
    "task_id",
)


def _row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {name: row.get(name) or "" for name in _TEXT_FIELDS}
    out.update(
        doc_id=row["doc_id"],
        version=int(row.get("version") or 0),
        reserved=bool(row["reserved"]),
        status=row["status"],
        total=int(row.get("total") or 0),
        enabled=int(row.get("enabled_n") or 0),
        model=row.get("model"),
        stages={stage: _stage_summary(row, stage) for stage in INDEX_STAGES},
    )
    return out


def list_chunk_rows(
    db: sqlite3.Connection, ref: tuple[str, str], page: tuple[int, int]
) -> tuple[list[dict[str, Any]], int]:
    tenant, doc_id = tenant_key(ref[0]), ref[1]
    total = int(
        db.execute(
            "SELECT COUNT(*) FROM chunks WHERE tenant=? AND doc_id=?", (tenant, doc_id)
        ).fetchone()[0]
    )
    rows = db.execute(
        "SELECT id,doc_id,position,revision,enabled,token_count,text_sha256,embedded,"
        "fulltext_indexed,graph_extracted,model,summary FROM chunks WHERE tenant=? AND doc_id=? "
        "ORDER BY position, id LIMIT ? OFFSET ?",
        (tenant, doc_id, page[0], page[1]),
    ).fetchall()
    return [chunk_projection(row) for row in rows], total


def chunk_projection(row: tuple[Any, ...]) -> dict[str, Any]:
    summary = json.loads(row[11] or "{}")
    return {
        "chunk_id": row[0],
        "doc_id": row[1],
        "ordinal": int(row[2]),
        "revision": int(row[3]),
        "enabled": bool(row[4]),
        "token_count": int(row[5] or 0),
        "text_sha256": row[6] or "",
        "flags": {"vector": bool(row[7]), "fulltext": bool(row[8]), "graph": bool(row[9])},
        "model": row[10] or "",
        "breadcrumb": list(summary.get("breadcrumb") or []),
        "text_preview": str(summary.get("preview") or ""),
        "stages": dict(summary.get("stages") or {}),
    }


def chunk_detail_row(
    db: sqlite3.Connection, ref: tuple[str, str, str]
) -> tuple[dict[str, Any], str] | None:
    """Projection plus the chunk payload JSON (one chunk only)."""
    tenant, doc_id, chunk_id = tenant_key(ref[0]), ref[1], ref[2]
    row = db.execute(
        "SELECT id,doc_id,position,revision,enabled,token_count,text_sha256,embedded,"
        "fulltext_indexed,graph_extracted,model,summary,payload FROM chunks "
        "WHERE tenant=? AND doc_id=? AND id=?",
        (tenant, doc_id, chunk_id),
    ).fetchone()
    return (chunk_projection(row[:12]), str(row[12])) if row else None
