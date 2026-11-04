"""Forward-compatible public response views, independent of the AG server package."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AuditStatus = Literal["persisted", "failed", "disabled"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Cost(WireModel):
    llm_calls: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class Claim(WireModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ToolCall(WireModel):
    tool: str
    reason: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    hits: list[str] = Field(default_factory=list)


class Step(WireModel):
    hop: int
    sub_question: str
    depends_on: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    conclusion: str = ""
    critic_action: str = ""


class EvidencePreview(WireModel):
    id: str
    content: str = ""
    source: str = ""
    score: float = 0


class DegradedChannel(WireModel):
    channel: str
    reason: str = ""
    status: str = "degraded"


class QueryResult(WireModel):
    """Common result view for REST results and SSE answer chains.

    Extra fields (including a streamed chain's schema_version) remain available
    in model_extra/model_dump. The raw SSE payload also remains on StreamEvent.
    """

    query_id: str
    question: str
    answer: str
    status: Literal["answered", "partial", "no_answer"]
    route: Literal["fast_path", "agentic", "chitchat"] = "agentic"
    claims: list[Claim] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    cost: Cost = Field(default_factory=Cost)
    evidence: list[EvidencePreview] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    explored_paths: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    degraded_channels: list[DegradedChannel] = Field(default_factory=list)
    low_confidence: bool = False
    audit_status: AuditStatus | None = None

    @model_validator(mode="after")
    def chain_audit_status(self) -> "QueryResult":
        value = self.metadata.get("audit_status")
        if self.audit_status is None and value in {"persisted", "failed", "disabled"}:
            self.audit_status = value
        return self


class KnowledgeBase(WireModel):
    kb_id: str
    tenant_id: str = ""
    name: str = ""
    build_mode: Literal["schema", "open"] = "schema"
    config_revision: int = 1
    active_generation: str | int = 1
    schema_id: str | None = None
    schema_version: str | None = None
    model_profile_id: str | None = None
    review_policy: str = "auto"
    global_summary_enabled: bool = False


class KnowledgeBasePage(WireModel):
    items: list[KnowledgeBase]
    total: int | None = None
    page: int = 1
    limit: int = 50
