"""Regex-based PII redaction hooks for logging and audit chains (ENT-06).

Default OFF — enable via ``AGR_REDACTION_ENABLED=1``.  Pattern selection
via ``AGR_REDACTION_PATTERNS`` (comma-separated names, or ``all``).
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.governance.audit_identifiers import AUDIT_IDENTIFIER_KEYS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENV_ENABLED = "AGR_REDACTION_ENABLED"
ENV_PATTERNS = "AGR_REDACTION_PATTERNS"

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

    def redact_dict(
        self, data: dict[str, Any], *, kept_keys: frozenset[str] = frozenset()
    ) -> dict[str, Any]:
        """Deep-redact all string values in *data*.

        Returns a **new** dict — the original is never mutated.
        Lists and nested dicts are traversed up to ``MAX_DEPTH``.
        ``kept_keys`` exempts machine identifiers, never prose-bearing objects.
        """
        if not self._enabled:
            return data
        result = _walk(data, self._redact_value, depth=0, kept_keys=kept_keys)
        assert isinstance(result, dict)
        return result

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
    transform: Callable[[str], str],
    *,
    depth: int,
    kept_keys: frozenset[str],
) -> Any:
    """Recursively transform string leaves inside dicts/lists."""
    if depth > MAX_DEPTH:
        return obj
    if isinstance(obj, str):
        return transform(obj)
    if isinstance(obj, dict):
        return {
            k: v
            if k in kept_keys and _identifier_value(k, v)
            else _walk(v, transform, depth=depth + 1, kept_keys=kept_keys)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_walk(item, transform, depth=depth + 1, kept_keys=kept_keys) for item in obj]
    return obj


def _identifier_value(key: str, value: Any) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, list):
        return all(isinstance(item, str) for item in value)
    return (
        key == "prompt_versions"
        and isinstance(value, dict)
        and all(isinstance(item, str) for item in value.values())
    )


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

# Correlation keys are system identifiers, not personal data. The digit patterns
# would otherwise rewrite them and break log joins, audit lookup and tenant checks.
_LOG_KEPT_KEYS = ("query_id", "request_id", "tenant_id")


def _restore(
    original: dict[str, Any], redacted: dict[str, Any], keys: tuple[str, ...]
) -> dict[str, Any]:
    return {**redacted, **{key: original[key] for key in keys if key in original}}


def redact_log_record(record: dict[str, Any]) -> dict[str, Any]:
    """Hook for ``logging_setup.py`` JSON formatter.

    Usage in a logging formatter::

        record_dict = redact_log_record(record_dict)
    """
    redacted = get_redactor().redact_dict(record)
    return record if redacted is record else _restore(record, redacted, _LOG_KEPT_KEYS)


def redact_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Hook for audit chain persistence.

    Call before writing a ``ReasoningChain`` dict to the audit store.
    """
    return get_redactor().redact_dict(payload, kept_keys=AUDIT_IDENTIFIER_KEYS)
