"""Map/reduce findings retain a verifiable, closed set of original source references."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agentic_graphrag.agent.global_units import finding_tokens
from agentic_graphrag.core.enums import PromptName
from agentic_graphrag.core.knowledge import SourceRef, SummaryFinding, SummaryRecord
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.execution import check_execution
from agentic_graphrag.knowledge.summarizer import FindingsPayload, ask_findings


@dataclass(frozen=True)
class MappedFinding:
    id: str
    text: str
    sources: tuple[SourceRef, ...]
    importance: float


def map_report(question: str, report: SummaryRecord, *, llm: LLMClient) -> list[MappedFinding]:
    inputs = {
        f"{report.summary_id}:{index}": finding for index, finding in enumerate(report.findings)
    }
    if not inputs:
        return []
    payload = ask_findings(
        llm,
        PromptName.GLOBAL_MAP,
        {
            "question": question,
            "findings": [
                {"id": key, "text": value.text, "importance": value.importance}
                for key, value in inputs.items()
            ],
        },
    )
    return bind_mapped(payload, inputs)


def reduce_findings(
    question: str,
    findings: list[MappedFinding],
    *,
    llm: LLMClient,
    partial: bool,
    batch_size: int,
    token_limit: int = 4000,
) -> list[MappedFinding]:
    current = findings
    while current:
        groups = _batches(current, batch_size=batch_size, token_limit=token_limit)
        if len(current) == 1:
            return current
        reduced: list[MappedFinding] = []
        for group in groups:
            reduced.extend(_reduce_batch(question, group, llm=llm, partial=partial))
        if len(groups) == 1 or len(reduced) >= len(current):
            # The model did not compress; do not loop or silently drop findings.
            return reduced
        current = reduced
    return []


def _batches(
    findings: list[MappedFinding], *, batch_size: int, token_limit: int
) -> list[list[MappedFinding]]:
    result: list[list[MappedFinding]] = []
    current: list[MappedFinding] = []
    tokens = 0
    for finding in findings:
        size = finding_tokens(finding.text)
        if size > token_limit:
            raise ValueError("A finding exceeds the reduction input budget")
        if current and (len(current) >= batch_size or tokens + size > token_limit):
            result.append(current)
            current, tokens = [], 0
        current.append(finding)
        tokens += size
    return [*result, current] if current else result


def _reduce_batch(
    question: str, findings: list[MappedFinding], *, llm: LLMClient, partial: bool
) -> list[MappedFinding]:
    check_execution()
    inputs = {item.id: item for item in findings}
    payload = ask_findings(
        llm,
        PromptName.GLOBAL_REDUCE,
        {
            "question": question,
            "partial_coverage": partial,
            "findings": [
                {"id": item.id, "text": item.text, "importance": item.importance}
                for item in findings
            ],
        },
    )
    return bind_mapped(payload, inputs)


def bind_mapped(
    payload: FindingsPayload, inputs: dict[str, SummaryFinding] | dict[str, MappedFinding]
) -> list[MappedFinding]:
    result = []
    for item in payload.findings:
        if any(key not in inputs for key in item.source_ids):
            raise ValueError("Mapped finding cites unknown input findings")
        refs = {
            ref.model_dump_json(): ref for key in item.source_ids for ref in inputs[key].sources
        }
        identity = hashlib.sha256((item.text + "".join(sorted(refs))).encode()).hexdigest()[:24]
        result.append(MappedFinding(identity, item.text, tuple(refs.values()), item.importance))
    return result
