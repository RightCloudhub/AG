"""Summary task ownership, retry and publication contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.core.knowledge import KnowledgeScope


class SummaryJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    job_id: str
    scope: KnowledgeScope
    generation: str
    kind: Literal["document", "community"]
    artifact_id: str
    input_fingerprint: str
    model_revision: str
    prompt_version: str
    status: Literal["pending", "running", "completed", "failed", "superseded"] = "pending"
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=20)
    owner: str = ""
    lease_token: str = ""
    lease_until: float = 0
    error_code: str = ""
