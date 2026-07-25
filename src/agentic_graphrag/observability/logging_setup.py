"""Structured JSON logging with context propagation (ENT-01).

Integration
-----------
Call ``setup_logging()`` in FastAPI lifespan.  Middleware should call
``bind_context()`` on each request and ``clear_context()`` in finally.
The ``redact_hook`` can be replaced externally for ENT-06 PII redaction.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LOG_LEVEL = "INFO"
_ENV_LOG_LEVEL = "AGR_LOG_LEVEL"
_ENV_LOG_FILE = "AGR_LOG_FILE"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5
_ROOT_LOGGER_NAME = "agentic_graphrag"

# ---------------------------------------------------------------------------
# Context variables — carry request-scoped IDs across async boundaries
# ---------------------------------------------------------------------------

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
query_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("query_id", default="")
tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")

# ---------------------------------------------------------------------------
# Redact hook — replaceable externally for ENT-06 PII redaction
# ---------------------------------------------------------------------------

RedactHook = Callable[[dict[str, Any]], dict[str, Any]]


def _identity_redact(record: dict[str, Any]) -> dict[str, Any]:
    """Default redact hook: pass-through (no redaction)."""
    return record


_redact_hook: RedactHook = _identity_redact


def set_redact_hook(hook: RedactHook) -> None:
    """Replace the global redact hook (ENT-06 integration point)."""
    global _redact_hook  # noqa: PLW0603
    _redact_hook = hook


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "query_id": query_id_var.get(),
            "tenant_id": tenant_id_var.get(),
            "user_id": user_id_var.get(),
        }

        # Merge extra fields (skip internal LogRecord attrs)
        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _LOGRECORD_BUILTIN_ATTRS and not k.startswith("_")
        }
        if extra:
            entry["extra"] = extra

        # Include stacktrace on ERROR/CRITICAL
        if record.exc_info and record.levelno >= logging.ERROR:
            entry["stacktrace"] = "".join(traceback.format_exception(*record.exc_info))

        entry = _redact_hook(entry)
        return json.dumps(entry, default=str, ensure_ascii=False)


# Built-in LogRecord attribute names to exclude from 'extra'
_LOGRECORD_BUILTIN_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stackInfo",
        "taskName",
        "thread",
        "threadName",
    }
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_CONFIGURED: bool = False


def setup_logging(
    level: str | None = None,
    log_file: str | None = None,
) -> None:
    """Configure structured JSON logging for the application.

    Parameters
    ----------
    level : str | None
        Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        Falls back to ``AGR_LOG_LEVEL`` env, then INFO.
    log_file : str | None
        Optional file path for rotated log output.
        Falls back to ``AGR_LOG_FILE`` env.
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return
    _CONFIGURED = True

    resolved_level = (level or os.environ.get(_ENV_LOG_LEVEL) or _DEFAULT_LOG_LEVEL).upper()
    resolved_file = log_file or os.environ.get(_ENV_LOG_FILE)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(resolved_level)

    formatter = JSONFormatter()

    # Console handler (stderr)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Optional rotating file handler
    if resolved_file:
        file_handler = RotatingFileHandler(
            resolved_file,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Prevent duplicate propagation to root
    root.propagate = False


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the application namespace."""
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def bind_context(
    *,
    request_id: str | None = None,
    query_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Set context variables for the current execution context."""
    if request_id is not None:
        request_id_var.set(request_id)
    if query_id is not None:
        query_id_var.set(query_id)
    if tenant_id is not None:
        tenant_id_var.set(tenant_id)
    if user_id is not None:
        user_id_var.set(user_id)


def clear_context() -> None:
    """Reset all context variables to their defaults."""
    request_id_var.set("")
    query_id_var.set("")
    tenant_id_var.set("")
    user_id_var.set("")
