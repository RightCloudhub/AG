"""Set-precision scoring: catches answer stuffing that containment scoring rewards.

``score_pair`` passes whenever the gold string is *contained* in the
prediction, so listing every candidate entity scores 100%. This module adds a
report-only strict view over entity-valued golds:

* ``set_recall``  — every gold entity is named in the prediction;
* ``exact_set``   — the prediction names exactly the gold entities (entities
  already named in the question are ignored, so "The CEO of X is Y" is fine);
* ``extra_entities`` — total known entities named beyond the gold set.

Only cases whose every gold part is a known graph entity are counted, so open,
yes/no and no-answer cases never enter the denominator.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_SPLIT = re.compile(r"\s*(?:,|;|\band\b)\s*", re.IGNORECASE)


class EntityMatcher:
    """Longest-first, word-bounded entity mention finder over a fixed lexicon."""

    def __init__(self, names: Iterable[str]) -> None:
        uniq = {n.strip() for n in names if n and n.strip()}
        self._by_lower = {n.lower(): n for n in uniq}
        ordered = sorted(self._by_lower, key=len, reverse=True)
        body = "|".join(re.escape(n) for n in ordered) or r"(?!x)x"
        self._pattern = re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)

    def canonical(self, text: str) -> str | None:
        return self._by_lower.get(text.strip().lower())

    def find(self, text: str) -> set[str]:
        return {self._by_lower[m.group(0).lower()] for m in self._pattern.finditer(text or "")}


@dataclass
class StrictSummary:
    total: int = 0
    set_recall: int = 0
    exact_set: int = 0
    extra_entities: int = 0

    def to_dict(self) -> dict[str, Any]:
        rate = (self.exact_set / self.total) if self.total else 0.0
        return {
            "total": self.total,
            "set_recall": self.set_recall,
            "exact_set": self.exact_set,
            "exact_set_pct": round(rate * 100, 2),
            "extra_entities": self.extra_entities,
        }


def gold_entity_set(row: Mapping[str, Any], matcher: EntityMatcher) -> set[str] | None:
    """Gold as a set of canonical entity names, or None when not entity-valued."""
    names = [matcher.canonical(p) for p in _gold_parts(row)]
    if not names or None in names:
        return None
    return {n for n in names if n}


def _gold_parts(row: Mapping[str, Any]) -> list[str]:
    raw = row.get("gold_set")
    if isinstance(raw, list) and raw:
        return [str(p) for p in raw]
    return [p for p in _SPLIT.split(str(row.get("gold") or "")) if p.strip()]


def strict_summary(rows: Iterable[Mapping[str, Any]], matcher: EntityMatcher) -> StrictSummary:
    summary = StrictSummary()
    for row in rows:
        gold = gold_entity_set(row, matcher)
        if gold is None:
            continue
        mentioned = matcher.find(str(row.get("question") or "")) - gold
        named = matcher.find(str(row.get("prediction") or "")) - mentioned
        summary.total += 1
        summary.set_recall += int(gold <= named)
        summary.exact_set += int(named == gold)
        summary.extra_entities += len(named - gold)
    return summary
