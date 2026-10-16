"""Retention pruning helpers for enterprise JSONL stores (ENT-06)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class PruneTarget:
    path: Path
    days: int
    timestamp_fields: tuple[str, ...]


@dataclass(frozen=True)
class PruneResult:
    path: Path
    kept: int
    removed: int
    malformed: int


def prune_jsonl(target: PruneTarget, *, now: float, dry_run: bool = False) -> PruneResult:
    """Keep current and malformed rows; remove only provably expired rows."""
    if not target.path.exists():
        return PruneResult(target.path, 0, 0, 0)
    cutoff = now - target.days * _SECONDS_PER_DAY
    kept_lines: list[str] = []
    removed = malformed = 0
    for line in target.path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = _parse_row(line)
        if row is None:
            malformed += 1
            kept_lines.append(line)
            continue
        timestamp = _timestamp(row, target.timestamp_fields)
        if timestamp is not None and timestamp < cutoff:
            removed += 1
        else:
            kept_lines.append(line)
    if not dry_run and removed:
        _atomic_write(target.path, kept_lines)
    return PruneResult(target.path, len(kept_lines), removed, malformed)


def _parse_row(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _timestamp(row: dict[str, Any], fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = row.get(field)
        if value is None and field == "created_at":
            value = (row.get("metadata") or {}).get("created_at")
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _atomic_write(path: Path, lines: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    temporary.replace(path)
