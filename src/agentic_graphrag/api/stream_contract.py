"""Versioned schemas for decoded SSE frames; heartbeats remain comment lines."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.api.envelope import ErrorBody
from agentic_graphrag.app.contracts import DegradedChannelOut
from agentic_graphrag.core.reasoning import ReasoningChain


class ProgressPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    query_id: str


class TriagePayload(ProgressPayload):
    route: str
    rationale: str = ""
    estimated_hops: int = 0
    confidence: float = 0.0
    rule_hit: str = ""


class ThinkingPayload(ProgressPayload):
    stage: str
    text: str
    detail: str = ""


class SubQuestionPayload(ProgressPayload):
    hop: int = Field(ge=0)
    sub_question: str


class HopDonePayload(ProgressPayload):
    hop: int = Field(ge=0)
    conclusion: str = ""
    critic_action: str = ""


class DegradedPayload(ProgressPayload):
    channels: list[DegradedChannelOut]
    low_confidence: bool


class GlobalProgressPayload(ProgressPayload):
    stage: str
    unit: str
    processed_documents: int = Field(ge=0)
    total_documents: int = Field(ge=0)


class ErrorPayload(ErrorBody):
    query_id: str


EVENT_PAYLOADS: dict[str, type[BaseModel]] = {
    "cache_hit": ProgressPayload,
    "triage": TriagePayload,
    "thinking": ThinkingPayload,
    "sub_question": SubQuestionPayload,
    "hop_done": HopDonePayload,
    "degraded": DegradedPayload,
    "global_progress": GlobalProgressPayload,
    "answer": ReasoningChain,
    "error": ErrorPayload,
}


def sse_schema() -> dict[str, Any]:
    definitions: dict[str, Any] = {}
    variants = []
    for event, model in EVENT_PAYLOADS.items():
        payload = model.model_json_schema()
        definitions.update(payload.pop("$defs", {}))
        definitions[model.__name__] = payload
        variants.append(
            {
                "type": "object",
                "required": ["event", "id", "data"],
                "properties": {
                    "event": {"const": event},
                    "id": {"type": "string", "pattern": r"^.+:[1-9][0-9]*$"},
                    "data": {"$ref": f"#/$defs/{model.__name__}"},
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AgenticGraphRAG v1 SSE decoded frame",
        "description": "One terminal answer or error. Ignore unknown events. No POST replay.",
        "oneOf": variants,
        "$defs": definitions,
        "x-heartbeat": ": keep-alive",
        "x-contract-version": "1.1.0",
    }
