"""Truthy env-flag helpers for API runtime switches.

Read ``os.environ`` at call time (not import time) so tests can monkeypatch and
process-local flips remain valid. Keep AGR_* flags out of ``config.py`` to
preserve that call-time semantics (see docs/ARCHITECTURE.md §5 / P-A1).
"""

from __future__ import annotations

import os

_TRUTHY = frozenset({"1", "true", "yes"})


def env_flag(name: str) -> bool:
    """Return True when env var ``name`` is a truthy string (1/true/yes)."""
    return os.environ.get(name, "").lower() in _TRUTHY
