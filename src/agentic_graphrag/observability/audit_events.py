"""Security and management event audit log (ENT-03).

Append-only JSONL store for auth failures, rate-limit hits, budget trips,
review decisions, doc uploads, feedback submissions, and config changes.
Complements ``generation/audit_store.py`` (query-level reasoning chains)
with security/management events.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.config import resolve_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event action constants
# ---------------------------------------------------------------------------

AUTH_FAILURE = "auth_failure"
RATE_LIMITED = "rate_limited"
BUDGET_EXCEEDED = "budget_exceeded"
REVIEW_DECISION = "review_decision"
DOC_UPLOAD = "doc_upload"
FEEDBACK_SUBMITTED = "feedback_submitted"
CONFIG_CHANGE = "config_change"

# ---------------------------------------------------------------------------
# ContextVars — set by API middleware so emit_audit_event() can auto-fill
# ---------------------------------------------------------------------------

ctx_tenant_id: ContextVar[str] = ContextVar("ctx_tenant_id", default="")
ctx_user_id: ContextVar[str] = ContextVar("ctx_user_id", default="")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_DEFAULT_PATH = "data/processed/audit_events.jsonl"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_DEFAULT_MAX_BACKUPS = 5
_MAX_MEMORY_EVENTS = 10_000


@dataclass
class AuditEvent:
    """Single security / management audit event."""

    ts: float
    action: str
    tenant_id: str
    user_id: str
    target: str
    outcome: str
    details: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuditEvent:
        return cls(
            ts=float(d.get("ts") or 0.0),
            action=str(d.get("action") or ""),
            tenant_id=str(d.get("tenant_id") or ""),
            user_id=str(d.get("user_id") or ""),
            target=str(d.get("target") or ""),
            outcome=str(d.get("outcome") or ""),
            details=dict(d.get("details") or {}),
            event_id=str(d.get("event_id") or uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class AuditEventStore:
    """Thread-safe append-only JSONL store with size-based rotation."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        max_backups: int = _DEFAULT_MAX_BACKUPS,
    ) -> None:
        self.path: Path = Path(path) if path else resolve_path(_DEFAULT_PATH)
        self.max_bytes = max_bytes
        self.max_backups = max_backups
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()
        if self.path.exists():
            self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load existing events from JSONL (capped to newest entries)."""
        raw: list[AuditEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw.append(AuditEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        self._events = raw[-_MAX_MEMORY_EVENTS:]

    def _rotate(self) -> None:
        """Rename current file and keep at most ``max_backups`` old copies."""
        if not self.path.exists():
            return
        # Shift existing backups: .4 → .5 (dropped), .3 → .4, …, .1 → .2
        for i in range(self.max_backups, 0, -1):
            src = self.path.with_suffix(f".jsonl.{i}")
            if not src.exists():
                continue
            if i >= self.max_backups:
                src.unlink()
            else:
                src.rename(self.path.with_suffix(f".jsonl.{i + 1}"))
        self.path.rename(self.path.with_suffix(".jsonl.1"))

    def _needs_rotation(self) -> bool:
        try:
            return self.path.exists() and self.path.stat().st_size >= self.max_bytes
        except OSError:
            return False

    # -- public API ---------------------------------------------------------

    def record(self, event: AuditEvent) -> None:
        """Append *event* to the in-memory list and JSONL file."""
        with self._lock:
            self._events.append(event)
            if len(self._events) > _MAX_MEMORY_EVENTS:
                self._events = self._events[-_MAX_MEMORY_EVENTS:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            if self._needs_rotation():
                self._rotate()

    def list_events(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        tenant_id: str | None = None,
        action: str | None = None,
        limit: int = 200,
    ) -> list[AuditEvent]:
        """Return filtered events from the in-memory list."""
        with self._lock:
            items = list(self._events)
        filtered = _filter_events(
            items,
            since=since,
            until=until,
            tenant_id=tenant_id,
            action=action,
        )
        return filtered[-limit:]


# ---------------------------------------------------------------------------
# Filter helper (module-level to keep class lean)
# ---------------------------------------------------------------------------


def _filter_events(
    items: list[AuditEvent],
    *,
    since: float | None,
    until: float | None,
    tenant_id: str | None,
    action: str | None,
) -> list[AuditEvent]:
    preds: list[Any] = []
    if since is not None:
        preds.append(lambda e, s=since: e.ts >= s)
    if until is not None:
        preds.append(lambda e, u=until: e.ts <= u)
    if tenant_id is not None:
        preds.append(lambda e, t=tenant_id: e.tenant_id == t)
    if action is not None:
        preds.append(lambda e, a=action: e.action == a)
    if not preds:
        return items
    return [e for e in items if all(p(e) for p in preds)]


# ---------------------------------------------------------------------------
# Global singleton (lazy)
# ---------------------------------------------------------------------------

_STORE: AuditEventStore | None = None
_STORE_LOCK = threading.Lock()


def get_audit_event_store(path: Path | None = None) -> AuditEventStore:
    """Return (and lazily create) the global ``AuditEventStore``."""
    global _STORE  # noqa: PLW0603
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = AuditEventStore(path)
    return _STORE


# ---------------------------------------------------------------------------
# Convenience emitter
# ---------------------------------------------------------------------------


def emit_audit_event(
    action: str,
    *,
    tenant_id: str = "",
    user_id: str = "",
    target: str = "",
    outcome: str = "",
    **details: Any,
) -> None:
    """Build an ``AuditEvent`` and record it to the global store.

    *tenant_id* and *user_id* fall back to the current ``ContextVar``
    values (typically set by API middleware) when not supplied explicitly.
    """
    event = AuditEvent(
        ts=time.time(),
        action=action,
        tenant_id=tenant_id or ctx_tenant_id.get(),
        user_id=user_id or ctx_user_id.get(),
        target=target,
        outcome=outcome,
        details=details,
    )
    try:
        get_audit_event_store().record(event)
    except Exception:
        logger.exception("Failed to record audit event %s", event.event_id)
