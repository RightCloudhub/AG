"""Role and capability vocabulary independent of provider implementations."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelRole(StrEnum):
    TRIAGE = "triage"
    RESEARCHER = "researcher"
    VERIFIER = "verifier"
    WRITER = "writer"
    EXTRACTOR = "extractor"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completion: bool = True
    structured_output: Literal["json_schema", "json_object", "text"] = "json_object"
    embedding: bool = False
    embedding_dimensions: int | None = Field(default=None, ge=1, le=131_072)
    rerank: bool = False
    rerank_protocol: Literal["tei", "compatible"] = "compatible"
    temperature: bool = True


class ModelProfile(BaseModel):
    """One immutable revision; activation is a separate compare-and-swap pointer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    tenant_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=1, ge=1)
    roles: dict[ModelRole, str | None]
    catalog_fingerprint: str = ""
    created_at: str = ""


class CapabilityCheck(BaseModel):
    role: ModelRole
    model_id: str
    capability: str
    status: Literal["passed", "failed", "disabled"]
    error_code: str = ""
    latency_ms: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)


class ModelProfileTest(BaseModel):
    profile_id: str
    revision: int
    catalog_fingerprint: str = ""
    checks: list[CapabilityCheck] = Field(default_factory=list)
    tested_at: str = ""

    @property
    def passed(self) -> bool:
        required = set(ModelRole) - {ModelRole.RERANK}
        checked = {row.role for row in self.checks if row.status == "passed"}
        return required <= checked and all(row.status != "failed" for row in self.checks)
