"""Generate bounded reports while retaining finding-to-original-source lineage."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.config import load_prompt
from agentic_graphrag.config_summary import SummaryConfig
from agentic_graphrag.core.enums import MessageRole, PromptName
from agentic_graphrag.core.knowledge import SummaryFinding, SummaryRecord
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.llm import LLMClient, Message, Tier
from agentic_graphrag.knowledge.summary_inputs import SummaryInput, batches
from agentic_graphrag.llm.roles import for_role
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.observability.degradation import record_degradation


class FindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] = Field(min_length=1, max_length=100)
    importance: float = Field(default=0.5, ge=0, le=1)


class FindingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[FindingPayload] = Field(default_factory=list, max_length=100)


def ask_findings(llm: LLMClient, prompt: PromptName, data: dict[str, object]) -> FindingsPayload:
    return complete_structured(
        for_role(llm, ModelRole.WRITER),
        [
            Message(role=MessageRole.SYSTEM, content=load_prompt(prompt)),
            Message(role=MessageRole.USER, content=json.dumps(data, ensure_ascii=False)),
        ],
        FindingsPayload,
        tier=Tier.STRONG,
    )


def prompt_version(prompt: PromptName) -> str:
    return hashlib.sha256(load_prompt(prompt).encode()).hexdigest()


def summarize(
    record: SummaryRecord,
    inputs: list[SummaryInput],
    *,
    llm: LLMClient,
    config: SummaryConfig,
    context: str = "",
) -> SummaryRecord:
    prompt = (
        PromptName.DOCUMENT_SUMMARY if record.kind == "document" else PromptName.COMMUNITY_SUMMARY
    )
    groups = batches(inputs, token_limit=config.source_tokens_per_call)
    findings: list[SummaryFinding] = []
    failures = 0
    for group in groups:
        try:
            result = ask_findings(
                llm,
                prompt,
                {
                    "context": context,
                    "sources": [{"id": item.id, "text": item.text} for item in group],
                },
            )
            findings.extend(_bind_findings(result, group, limit=config.max_findings_per_call))
        except Exception as exc:
            failures += 1
            record_degradation("summary_generation", "summary_batch_failed", exc)
    status = "ready" if groups and not failures else "partial" if findings else "failed"
    unique = {item.model_dump_json(): item for item in findings}
    return record.model_copy(update={"findings": tuple(unique.values()), "status": status})


def _bind_findings(
    result: FindingsPayload, inputs: list[SummaryInput], *, limit: int
) -> list[SummaryFinding]:
    sources = {item.id: item.source for item in inputs}
    if len(result.findings) > limit:
        raise ValueError("Summary exceeds configured finding limit")
    if any(key not in sources for item in result.findings for key in item.source_ids):
        raise ValueError("Summary cites an unknown original source")
    return [
        SummaryFinding(
            text=item.text,
            importance=item.importance,
            sources=tuple(sources[key] for key in dict.fromkeys(item.source_ids)),
        )
        for item in result.findings
    ]
