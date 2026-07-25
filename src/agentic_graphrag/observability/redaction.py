"""Regex-based PII redaction hooks for logging and audit chains (ENT-06).

Default OFF — enable via ``AGR_REDACTION_ENABLED=1``.  Pattern selection
via ``AGR_REDACTION_PATTERNS`` (comma-separated names, or ``all``).
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENV_ENABLED = "AGR_REDACTION_ENABLED"
ENV_PATTERNS = "AGR_REDACTION_PATTERNS"

DEFAULT_ENABLED = False
DEFAULT_PATTERNS_SELECTOR = "all"

PLACEHOLDER_EMAIL = "[REDACTED_EMAIL]"
PLACEHOLDER_PHONE = "[REDACTED_PHONE]"
PLACEHOLDER_ID = "[REDACTED_ID]"

# Maximum recursion depth for nested dicts/lists in ``redact_dict``.
MAX_DEPTH = 10


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedactionPattern:
    """A single named redaction rule."""

    name: str
    pattern: re.Pattern[str]
    replacement: str


# ---------------------------------------------------------------------------
# Built-in patterns
# ---------------------------------------------------------------------------

_BUILTIN_PATTERNS: list[RedactionPattern] = [
    RedactionPattern(
        name="email",
        pattern=re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        ),
        replacement=PLACEHOLDER_EMAIL,
    ),
    # ID must precede phone patterns — an 18-digit ID contains phone-like substrings.
    RedactionPattern(
        name="id_cn",
        pattern=re.compile(r"\b\d{17}[\dXx]\b"),
        replacement=PLACEHOLDER_ID,
    ),
    RedactionPattern(
        name="phone_cn",
        pattern=re.compile(r"1[3-9]\d{9}"),
        replacement=PLACEHOLDER_PHONE,
    ),
    RedactionPattern(
        name="phone_intl",
        pattern=re.compile(
            r"\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}",
        ),
        replacement=PLACEHOLDER_PHONE,
    ),
]

_BUILTIN_BY_NAME: dict[str, RedactionPattern] = {p.name: p for p in _BUILTIN_PATTERNS}


def builtin_patterns() -> list[RedactionPattern]:
    """Return a copy of all built-in patterns."""
    return list(_BUILTIN_PATTERNS)


def builtin_pattern_names() -> list[str]:
    """Return names of all built-in patterns."""
    return list(_BUILTIN_BY_NAME.keys())


# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------


class Redactor:
    """Thread-safe, configurable PII redaction engine.

    Parameters
    ----------
    patterns:
        Explicit list of patterns.  ``None`` → use all built-in patterns.
    enabled:
        Master switch.  When *False*, ``redact`` / ``redact_dict`` are no-ops.
    """

    def __init__(
        self,
        patterns: list[RedactionPattern] | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self._enabled = enabled
        self._patterns: list[RedactionPattern] = (
            list(patterns) if patterns is not None else list(_BUILTIN_PATTERNS)
        )

    # -- properties ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def patterns(self) -> list[RedactionPattern]:
        return list(self._patterns)

    # -- public API ----------------------------------------------------------

    def redact(self, text: str) -> str:
        """Apply all active patterns to *text* and return the redacted string."""
        if not self._enabled:
            return text
        result = text
        for pat in self._patterns:
            result = pat.pattern.sub(pat.replacement, result)
        return result

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Deep-redact all string values in *data*.

        Returns a **new** dict — the original is never mutated.
        Lists and nested dicts are traversed up to ``MAX_DEPTH``.
        """
        if not self._enabled:
            return data
        return _walk(data, self._redact_value, depth=0)

    # -- internals -----------------------------------------------------------

    def _redact_value(self, value: str) -> str:
        """Redact a single string value."""
        result = value
        for pat in self._patterns:
            result = pat.pattern.sub(pat.replacement, result)
        return result


# ---------------------------------------------------------------------------
# Recursive walker (shared helper)
# ---------------------------------------------------------------------------


def _walk(
    obj: Any,
    transform: Any,
    *,
    depth: int,
) -> Any:
    """Recursively transform string leaves inside dicts/lists."""
    if depth > MAX_DEPTH:
        return obj
    if isinstance(obj, str):
        return transform(obj)
    if isinstance(obj, dict):
        return {k: _walk(v, transform, depth=depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(item, transform, depth=depth + 1) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _resolve_patterns(selector: str) -> list[RedactionPattern]:
    """Parse a comma-separated pattern selector into a pattern list."""
    selector = selector.strip().lower()
    if selector in {"all", ""}:
        return list(_BUILTIN_PATTERNS)
    names = [n.strip() for n in selector.split(",") if n.strip()]
    resolved: list[RedactionPattern] = []
    for name in names:
        if name in _BUILTIN_BY_NAME:
            resolved.append(_BUILTIN_BY_NAME[name])
    return resolved if resolved else list(_BUILTIN_PATTERNS)


def _create_from_env() -> Redactor:
    """Build a ``Redactor`` from environment variables."""
    enabled = os.getenv(ENV_ENABLED, "0").strip() in {"1", "true", "yes"}
    selector = os.getenv(ENV_PATTERNS, DEFAULT_PATTERNS_SELECTOR)
    patterns = _resolve_patterns(selector)
    return Redactor(patterns=patterns, enabled=enabled)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_GLOBAL: Redactor | None = None
_LOCK = threading.Lock()


def get_redactor() -> Redactor:
    """Return the process-wide ``Redactor`` singleton (created on first call)."""
    global _GLOBAL  # noqa: PLW0603
    if _GLOBAL is None:
        with _LOCK:
            if _GLOBAL is None:
                _GLOBAL = _create_from_env()
    return _GLOBAL


def reset_redactor() -> None:
    """Reset the global singleton (useful for tests)."""
    global _GLOBAL  # noqa: PLW0603
    with _LOCK:
        _GLOBAL = None


# ---------------------------------------------------------------------------
# Hook entry-points
# ---------------------------------------------------------------------------


def redact_log_record(record: dict[str, Any]) -> dict[str, Any]:
    """Hook for ``logging_setup.py`` JSON formatter.

    Usage in a logging formatter::

        record_dict = redact_log_record(record_dict)
    """
    return get_redactor().redact_dict(record)


def redact_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Hook for audit chain persistence.

    Call before writing a ``ReasoningChain`` dict to the audit store.
    """
    return get_redactor().redact_dict(payload)
