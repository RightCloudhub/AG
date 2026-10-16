"""Prompt registry: versioned prompts and per-run usage records (refactor §5.4).

Prompt text lives in ``configs/prompts/<name>.md`` (loaded by
:func:`agentic_graphrag.config.load_prompt`). A prompt's *version* is the first
12 hex chars of the SHA-256 of its text, so any edit is a new version without
manual bookkeeping. Each load is recorded in a context-local dict; drivers
attach :func:`prompt_versions` to the reasoning chain's metadata.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar

_USED: ContextVar[dict[str, str] | None] = ContextVar("agr_prompt_versions", default=None)


def prompt_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def record_prompt_use(name: str, text: str) -> str:
    version = prompt_version(text)
    used = _USED.get()
    if used is None:
        used = {}
        _USED.set(used)
    used[name] = version
    return version


def prompt_versions() -> dict[str, str]:
    """Prompts loaded in the current context, ``name → version``."""
    return dict(_USED.get() or {})


def reset_prompt_versions() -> None:
    _USED.set({})
