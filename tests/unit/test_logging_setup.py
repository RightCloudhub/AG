"""ENT-01: Structured logging setup tests."""

from __future__ import annotations

import json
import logging

import pytest

import agentic_graphrag.observability.logging_setup as mod
from agentic_graphrag.observability.logging_setup import (
    JSONFormatter,
    bind_context,
    clear_context,
    get_logger,
    query_id_var,
    request_id_var,
    set_redact_hook,
    setup_logging,
    tenant_id_var,
    user_id_var,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """Reset module state before and after each test."""
    mod._CONFIGURED = False
    clear_context()
    root = logging.getLogger(mod._ROOT_LOGGER_NAME)
    root.handlers.clear()
    yield
    mod._CONFIGURED = False
    clear_context()
    mod._redact_hook = mod._identity_redact
    root.handlers.clear()


# -- context vars ----------------------------------------------------------


def test_bind_and_clear_context() -> None:
    bind_context(request_id="r1", query_id="q1", tenant_id="t1", user_id="u1")
    assert request_id_var.get() == "r1"
    assert query_id_var.get() == "q1"
    assert tenant_id_var.get() == "t1"
    assert user_id_var.get() == "u1"

    clear_context()
    assert request_id_var.get() == ""
    assert query_id_var.get() == ""
    assert tenant_id_var.get() == ""
    assert user_id_var.get() == ""


def test_bind_partial_context() -> None:
    bind_context(request_id="r2")
    assert request_id_var.get() == "r2"
    assert query_id_var.get() == ""  # unchanged


# -- JSONFormatter ---------------------------------------------------------


def test_json_formatter_basic() -> None:
    fmt = JSONFormatter()
    record = logging.LogRecord("test.logger", logging.INFO, "", 0, "hello", (), None)
    output = fmt.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "hello"
    assert parsed["logger"] == "test.logger"
    assert "ts" in parsed


def test_json_formatter_includes_context_vars() -> None:
    bind_context(request_id="req-x", tenant_id="t-y")
    fmt = JSONFormatter()
    record = logging.LogRecord("x", logging.DEBUG, "", 0, "msg", (), None)
    parsed = json.loads(fmt.format(record))
    assert parsed["request_id"] == "req-x"
    assert parsed["tenant_id"] == "t-y"
    assert parsed["query_id"] == ""


def test_json_formatter_extra_fields() -> None:
    fmt = JSONFormatter()
    record = logging.LogRecord("x", logging.INFO, "", 0, "msg", (), None)
    record.custom_key = "custom_value"  # type: ignore[attr-defined]
    parsed = json.loads(fmt.format(record))
    assert parsed["extra"]["custom_key"] == "custom_value"


def test_json_formatter_stacktrace_on_error() -> None:
    fmt = JSONFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord("x", logging.ERROR, "", 0, "fail", (), exc_info)
    parsed = json.loads(fmt.format(record))
    assert "stacktrace" in parsed
    assert "boom" in parsed["stacktrace"]


def test_json_formatter_no_stacktrace_on_warning() -> None:
    """Stacktrace is only included for ERROR and above."""
    fmt = JSONFormatter()
    try:
        raise ValueError("warn-boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord("x", logging.WARNING, "", 0, "warn", (), exc_info)
    parsed = json.loads(fmt.format(record))
    assert "stacktrace" not in parsed


# -- setup_logging ---------------------------------------------------------


def test_setup_logging_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGR_LOG_LEVEL", raising=False)
    monkeypatch.delenv("AGR_LOG_FILE", raising=False)
    setup_logging()
    root = logging.getLogger(mod._ROOT_LOGGER_NAME)
    assert root.level == logging.INFO
    # Filter out pytest's LogCaptureHandler — only ours should remain
    our_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler) and not type(h).__name__.startswith("LogCapture")
    ]
    assert len(our_handlers) == 1


def test_setup_logging_custom_level() -> None:
    setup_logging(level="DEBUG")
    root = logging.getLogger(mod._ROOT_LOGGER_NAME)
    assert root.level == logging.DEBUG


def test_setup_logging_with_file(tmp_path: object) -> None:
    from pathlib import Path

    log_file = Path(str(tmp_path)) / "test.log"
    setup_logging(log_file=str(log_file))
    root = logging.getLogger(mod._ROOT_LOGGER_NAME)
    handler_types = [type(h).__name__ for h in root.handlers]
    assert "RotatingFileHandler" in handler_types


def test_setup_logging_env_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGR_LOG_LEVEL", "WARNING")
    setup_logging()
    root = logging.getLogger(mod._ROOT_LOGGER_NAME)
    assert root.level == logging.WARNING


def test_setup_logging_idempotent() -> None:
    setup_logging(level="DEBUG")
    setup_logging(level="ERROR")  # second call is a no-op
    root = logging.getLogger(mod._ROOT_LOGGER_NAME)
    assert root.level == logging.DEBUG


# -- get_logger ------------------------------------------------------------


def test_get_logger_namespace() -> None:
    log = get_logger("agent.loop")
    assert log.name == "agentic_graphrag.agent.loop"


# -- redact_hook -----------------------------------------------------------


def test_redact_hook() -> None:
    def mask_user(record: dict) -> dict:
        record["user_id"] = "***"
        return record

    set_redact_hook(mask_user)
    fmt = JSONFormatter()
    bind_context(user_id="secret-user")
    record = logging.LogRecord("x", logging.INFO, "", 0, "msg", (), None)
    parsed = json.loads(fmt.format(record))
    assert parsed["user_id"] == "***"
