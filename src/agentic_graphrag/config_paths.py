"""Repo-root discovery for config and data path resolution.

Split out of ``config.py`` so that module keeps headroom under the 300-line
gate. ``config.py`` calls :func:`find_root` once at import to set ``ROOT_DIR``;
every data path then resolves against it regardless of cwd.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT_ENV_VAR = "AGENTIC_GRAPHRAG_ROOT"


def find_root() -> Path:
    """Locate repo root (handles editable install, site-packages, and cwd)."""
    env = os.environ.get(ROOT_ENV_VAR)
    if env:
        return Path(env).resolve()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        found = _root_from_start(start)
        if found is not None:
            return found
    return _root_near_src() or Path.cwd().resolve()


def _root_from_start(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if _is_full_root(candidate) or _is_light_root(candidate):
            return candidate.resolve()
    return None


def _is_full_root(candidate: Path) -> bool:
    if not (candidate / "configs" / "default.yaml").exists():
        return False
    return (candidate / "pyproject.toml").exists() or (candidate / "PRD.md").exists()


def _is_light_root(candidate: Path) -> bool:
    return (candidate / "pyproject.toml").exists() and (candidate / "configs").exists()


def _root_near_src() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "src").exists():
            return parent
    return None
