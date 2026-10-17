"""Statistics for eval A/B decisions (ARCH_OPTIMIZATION_V2 §4 O-1).

* :func:`wilson_interval` — 95% CI for an accuracy.
* :func:`mcnemar` — exact paired test for two systems run on the same cases.
* :func:`breakdown` — accuracy per category / template.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

_Z95 = 1.959963984540054


def wilson_interval(correct: int, total: int, z: float = _Z95) -> tuple[float, float]:
    """Wilson score interval; ``(0.0, 1.0)`` when ``total`` is zero."""
    if total <= 0:
        return 0.0, 1.0
    p = correct / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class McNemarResult:
    """Discordant counts and exact two-sided p-value."""

    a_only: int  # A correct, B wrong
    b_only: int  # B correct, A wrong
    both: int
    neither: int
    p_value: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "a_only": self.a_only,
            "b_only": self.b_only,
            "both": self.both,
            "neither": self.neither,
            "p_value": round(self.p_value, 6),
        }


def _binom_two_sided(k: int, n: int) -> float:
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2**n
    return min(1.0, 2.0 * tail)


def mcnemar(a: Mapping[str, bool], b: Mapping[str, bool]) -> McNemarResult:
    """Exact McNemar test over case ids present in both runs."""
    ids = sorted(set(a) & set(b))
    a_only = sum(1 for i in ids if a[i] and not b[i])
    b_only = sum(1 for i in ids if b[i] and not a[i])
    both = sum(1 for i in ids if a[i] and b[i])
    neither = len(ids) - a_only - b_only - both
    p = _binom_two_sided(min(a_only, b_only), a_only + b_only)
    return McNemarResult(a_only, b_only, both, neither, p)


@dataclass
class GroupStat:
    correct: int = 0
    total: int = 0
    ids: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        lo, hi = wilson_interval(self.correct, self.total)
        return {
            "correct": self.correct,
            "total": self.total,
            "accuracy": round(self.accuracy, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
        }


def breakdown(
    results: Mapping[str, bool],
    cases: Mapping[str, Mapping[str, Any]],
    key: Callable[[Mapping[str, Any]], str],
) -> dict[str, GroupStat]:
    """Group per-case correctness by ``key(case)``."""
    out: dict[str, GroupStat] = defaultdict(GroupStat)
    for cid, ok in results.items():
        group = key(cases.get(cid, {})) or "unknown"
        stat = out[group]
        stat.total += 1
        stat.correct += int(bool(ok))
        stat.ids.append(cid)
    return dict(sorted(out.items()))


def category_of(case: Mapping[str, Any]) -> str:
    return str(case.get("category") or "")


def template_of(case: Mapping[str, Any]) -> str:
    return str((case.get("metadata") or {}).get("template") or "")


def percentile(values: Iterable[float], p: float) -> float:
    data = sorted(values)
    if not data:
        return 0.0
    idx = min(len(data) - 1, max(0, math.ceil(p / 100.0 * len(data)) - 1))
    return float(data[idx])
