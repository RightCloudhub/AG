"""Bound summary map and reduce inputs without dropping facts silently."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeVar

from agentic_graphrag.core.knowledge import SummaryFinding, SummaryRecord
from agentic_graphrag.knowledge.tokenizer import get_tokenizer

T = TypeVar("T")


@dataclass(frozen=True)
class MapUnit:
    report: SummaryRecord
    original_id: str


def report_units(report: SummaryRecord, *, token_limit: int) -> list[MapUnit]:
    groups: list[list[SummaryFinding]] = []
    current: list[SummaryFinding] = []
    tokens = 0
    for finding in report.findings:
        size = finding_tokens(finding.text)
        if size > token_limit:
            raise ValueError("A summary finding exceeds the map input budget")
        if current and tokens + size > token_limit:
            groups.append(current)
            current, tokens = [], 0
        current.append(finding)
        tokens += size
    groups.append(current)
    return [
        MapUnit(
            report.model_copy(
                update={
                    "summary_id": f"{report.summary_id}:{index}",
                    "findings": tuple(group),
                }
            ),
            report.summary_id,
        )
        for index, group in enumerate(groups)
    ]


def finding_tokens(text: str) -> int:
    # Include a conservative ID and structured-field allowance in the input limit.
    payload = json.dumps({"id": "x" * 64, "text": text, "importance": 1.0}, ensure_ascii=False)
    return get_tokenizer("cl100k_base").count(payload)
