"""SQL for tenant generations, document leases and durable operation records."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from agentic_graphrag.core.ports.document_ops import (
    ACTIVE_OP_STATES,
    CommandScope,
    DocOperation,
    Fence,
    IdempotencyConflict,
)

_OP_COLUMNS = "id,tenant,doc_id,kind,task_id,payload,state,dispatched,error,created_at,updated_at"


def tenant_key(tenant_id: str | None) -> str:
    return tenant_id or "default"


def generation(db: sqlite3.Connection, tenant: str) -> int:
    row = db.execute(
        "SELECT generation FROM generations WHERE tenant=?", (tenant_key(tenant),)
    ).fetchone()
    return int(row[0]) if row else 0


def bump_generation(db: sqlite3.Connection, tenant: str) -> int:
    db.execute(
        "INSERT INTO generations VALUES (?,1) "
        "ON CONFLICT(tenant) DO UPDATE SET generation=generation+1",
        (tenant_key(tenant),),
    )
    return generation(db, tenant)


def _ensure_state(db: sqlite3.Connection, tenant: str, doc_id: str) -> None:
    db.execute(
        "INSERT OR IGNORE INTO doc_state (tenant,doc_id) VALUES (?,?)", (tenant_key(tenant), doc_id)
    )


def mutation_seq(db: sqlite3.Connection, tenant: str, doc_id: str) -> int:
    row = db.execute(
        "SELECT mutation_seq FROM doc_state WHERE tenant=? AND doc_id=?",
        (tenant_key(tenant), doc_id),
    ).fetchone()
    return int(row[0]) if row else 0


def bump_mutation(db: sqlite3.Connection, tenant: str, doc_id: str) -> None:
    _ensure_state(db, tenant, doc_id)
    db.execute(
        "UPDATE doc_state SET mutation_seq=mutation_seq+1 WHERE tenant=? AND doc_id=?",
        (tenant_key(tenant), doc_id),
    )


def acquire_lease(
    db: sqlite3.Connection, ref: tuple[str, str], *, owner: str, ttl: float
) -> Fence | None:
    """Take or renew the document lease; a new holder gets a higher fencing token."""
    tenant, doc_id = tenant_key(ref[0]), ref[1]
    _ensure_state(db, tenant, doc_id)
    held, token, expires = db.execute(
        "SELECT lease_owner,lease_token,lease_expires FROM doc_state WHERE tenant=? AND doc_id=?",
        (tenant, doc_id),
    ).fetchone()
    now = time.time()
    if held and held != owner and expires > now:
        return None
    if held != owner or expires <= now:
        token = int(token) + 1
    db.execute(
        "UPDATE doc_state SET lease_owner=?,lease_token=?,lease_expires=? "
        "WHERE tenant=? AND doc_id=?",
        (owner, token, now + ttl, tenant, doc_id),
    )
    return Fence(tenant, doc_id, owner, int(token))


def fence_valid(db: sqlite3.Connection, fence: Fence) -> bool:
    row = db.execute(
        "SELECT 1 FROM doc_state WHERE tenant=? AND doc_id=? AND lease_owner=? "
        "AND lease_token=? AND lease_expires>?",
        (fence.tenant_id, fence.doc_id, fence.owner, fence.token, time.time()),
    ).fetchone()
    return row is not None


def renew_lease(db: sqlite3.Connection, fence: Fence, *, ttl: float) -> bool:
    if not fence_valid(db, fence):
        return False
    db.execute(
        "UPDATE doc_state SET lease_expires=? WHERE tenant=? AND doc_id=? AND lease_token=?",
        (time.time() + ttl, fence.tenant_id, fence.doc_id, fence.token),
    )
    return True


def release_lease(db: sqlite3.Connection, fence: Fence) -> None:
    db.execute(
        "UPDATE doc_state SET lease_owner='',lease_expires=0 "
        "WHERE tenant=? AND doc_id=? AND lease_owner=? AND lease_token=?",
        (fence.tenant_id, fence.doc_id, fence.owner, fence.token),
    )


def insert_op(db: sqlite3.Connection, op: DocOperation) -> None:
    db.execute(
        f"INSERT INTO ops ({_OP_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            op.op_id,
            tenant_key(op.tenant_id),
            op.doc_id,
            op.kind,
            op.task_id,
            json.dumps(op.payload, ensure_ascii=False),
            op.state,
            int(op.dispatched),
            op.error,
            op.created_at,
            op.updated_at,
        ),
    )


def _op(row: tuple[Any, ...]) -> DocOperation:
    return DocOperation(
        op_id=row[0],
        tenant_id=row[1],
        doc_id=row[2],
        kind=row[3],
        task_id=row[4],
        payload=json.loads(row[5] or "{}"),
        state=row[6],
        dispatched=bool(row[7]),
        error=row[8] or "",
        created_at=float(row[9]),
        updated_at=float(row[10]),
    )


def get_op(db: sqlite3.Connection, op_id: str) -> DocOperation | None:
    row = db.execute(f"SELECT {_OP_COLUMNS} FROM ops WHERE id=?", (op_id,)).fetchone()
    return _op(row) if row else None


def undispatched_ops(db: sqlite3.Connection, limit: int) -> list[DocOperation]:
    rows = db.execute(
        f"SELECT {_OP_COLUMNS} FROM ops WHERE dispatched=0 ORDER BY created_at,id LIMIT ?",
        (limit,),
    )
    return [_op(row) for row in rows]


def active_ops(db: sqlite3.Connection, tenant: str, doc_ids: list[str]) -> list[DocOperation]:
    marks = ",".join("?" * len(doc_ids))
    rows = db.execute(
        f"SELECT {_OP_COLUMNS} FROM ops WHERE tenant=? AND doc_id IN ({marks}) "
        f"ORDER BY created_at,id",
        (tenant_key(tenant), *doc_ids),
    )
    return [_op(row) for row in rows]


def update_op(db: sqlite3.Connection, op_id: str, **fields: Any) -> None:
    fields["updated_at"] = time.time()
    if "payload" in fields:
        fields["payload"] = json.dumps(fields["payload"], ensure_ascii=False)
    assignments = ",".join(f"{name}=?" for name in fields)
    db.execute(f"UPDATE ops SET {assignments} WHERE id=?", (*fields.values(), op_id))


def is_active(op: DocOperation) -> bool:
    return op.state in ACTIVE_OP_STATES


def replay(db: sqlite3.Connection, scope: CommandScope, digest: str) -> dict[str, Any] | None:
    """Stored response for a matching retry; 409 when the key carried another payload."""
    row = db.execute(
        "SELECT hash,response FROM idempotency WHERE tenant=? AND operation=? AND target=? "
        "AND key=?",
        (tenant_key(scope.tenant_id), scope.operation, scope.target, scope.key),
    ).fetchone()
    if row is None:
        return None
    if row[0] != digest:
        raise IdempotencyConflict("Idempotency-Key was already used with a different payload")
    result: dict[str, Any] = json.loads(row[1])
    return result


def remember(
    db: sqlite3.Connection,
    scope: CommandScope,
    *,
    digest: str,
    outcome: tuple[dict[str, Any], str],
) -> None:
    response, op_id = outcome
    db.execute(
        "INSERT INTO idempotency VALUES (?,?,?,?,?,?,?,?)",
        (
            tenant_key(scope.tenant_id),
            scope.operation,
            scope.target,
            scope.key,
            digest,
            json.dumps(response, ensure_ascii=False),
            op_id,
            time.time(),
        ),
    )
