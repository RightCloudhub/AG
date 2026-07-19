"""Eval case schema + stratification validator (P2-EV-01 substrate).

The ≥200-case set is mostly human/schedule work; this module provides the
codeable foundation: Pydantic case model, hop/category mix checks, and
JSONL load/dump helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CaseCategory(StrEnum):
    HOP2 = "2hop"
    HOP3 = "3hop"
    OPEN = "open"
    NO_ANSWER = "no_answer"


class EvalCase(BaseModel):
    """Single multi-hop eval case (gold answer + evidence path)."""

    id: str
    question: str
    gold_answer: str = ""
    hops: int = Field(default=0, ge=0, description="Nominal hop count; 0 for no_answer/open")
    category: CaseCategory | None = None
    gold_path: list[str] = Field(default_factory=list)
    gold_evidence: list[str] = Field(default_factory=list)
    notes: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v: object) -> object:
        if v is None or v == "":
            return None
        if isinstance(v, CaseCategory):
            return v
        s = str(v).lower().replace("-", "").replace("_", "")
        mapping = {
            "2hop": CaseCategory.HOP2,
            "hop2": CaseCategory.HOP2,
            "3hop": CaseCategory.HOP3,
            "hop3": CaseCategory.HOP3,
            "open": CaseCategory.OPEN,
            "openpath": CaseCategory.OPEN,
            "noanswer": CaseCategory.NO_ANSWER,
            "none": CaseCategory.NO_ANSWER,
        }
        return mapping.get(s, v)

    def resolved_category(self) -> CaseCategory:
        if self.category is not None:
            return self.category
        ga = (self.gold_answer or "").strip().lower()
        if ga in {"", "n/a", "none", "no answer", "unknown", "无法回答"}:
            return CaseCategory.NO_ANSWER
        if self.hops >= 3:
            return CaseCategory.HOP3
        if self.hops == 2:
            return CaseCategory.HOP2
        if self.hops <= 1 and self.gold_path:
            # open / variable hop
            return CaseCategory.OPEN
        return CaseCategory.OPEN


@dataclass
class StratificationSpec:
    """G2-oriented mix targets (evaluation.md §1)."""

    min_total: int = 200
    min_2hop: int = 90
    min_3hop: int = 60
    min_open: int = 30
    min_no_answer: int = 20


@dataclass
class StratificationReport:
    total: int
    by_category: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total": self.total,
            "by_category": self.by_category,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def load_cases(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        # Accept both gold_answer and legacy keys
        if "gold_answer" not in raw and "gold" in raw:
            raw = {**raw, "gold_answer": raw["gold"]}
        cases.append(EvalCase.model_validate(raw))
    return cases


def dump_cases(cases: list[EvalCase], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(c.model_dump_json() + "\n")
    return out


def validate_stratification(
    cases: list[EvalCase],
    spec: StratificationSpec | None = None,
    *,
    strict_total: bool = True,
) -> StratificationReport:
    """Validate hop/category mix for G2 evalset readiness.

    When ``strict_total`` is False, only ratio-style floors that fit current
    size are checked (useful for the small POC set).
    """
    spec = spec or StratificationSpec()
    report = StratificationReport(total=len(cases))
    counts: dict[str, int] = {c.value: 0 for c in CaseCategory}
    seen_ids: set[str] = set()

    for case in cases:
        if case.id in seen_ids:
            report.errors.append(f"duplicate id: {case.id}")
        seen_ids.add(case.id)
        if not case.question.strip():
            report.errors.append(f"{case.id}: empty question")
        cat = case.resolved_category()
        counts[cat.value] = counts.get(cat.value, 0) + 1
        if cat == CaseCategory.NO_ANSWER:
            continue
        if not (case.gold_answer or "").strip():
            report.warnings.append(f"{case.id}: empty gold_answer for non-no_answer case")

    report.by_category = counts

    if strict_total and report.total < spec.min_total:
        report.errors.append(f"total {report.total} < min_total {spec.min_total}")

    def _need(key: str, minimum: int) -> None:
        n = counts.get(key, 0)
        if strict_total:
            if n < minimum:
                report.errors.append(f"{key} count {n} < {minimum}")
        else:
            # Soft: only warn on empty required buckets for small sets
            if n == 0 and minimum > 0 and report.total >= 5:
                report.warnings.append(f"{key} count is 0 (target ≥{minimum})")

    _need(CaseCategory.HOP2.value, spec.min_2hop)
    _need(CaseCategory.HOP3.value, spec.min_3hop)
    _need(CaseCategory.OPEN.value, spec.min_open)
    _need(CaseCategory.NO_ANSWER.value, spec.min_no_answer)

    return report
