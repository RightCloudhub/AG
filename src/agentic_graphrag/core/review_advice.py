"""Shadow review recommendations and paired calibration receipts."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.core.knowledge import KnowledgeScope


class ReviewRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "abstain"]
    reason: str = Field(min_length=1, max_length=1500)


class ReviewAdvice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    review_id: str
    scope: KnowledgeScope
    generation: str
    proposer_revision: str
    verifier_revision: str
    decision: Literal["approve", "reject", "abstain"]
    reason: str
    verified: bool
    input_fingerprint: str


class ReviewPair(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    review_id: str
    agent_decision: Literal["approve", "reject", "abstain"]
    human_decision: Literal["approve", "reject"]


class ReviewCalibration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: KnowledgeScope
    dataset_id: str = Field(min_length=1)
    proposer_revision: str
    verifier_revision: str
    real_corpus: bool = False
    held_out: bool = False
    reviewed: int = Field(ge=0)
    approved: int = Field(ge=0)
    false_approvals: int = Field(ge=0)
    false_approval_upper: float = Field(ge=0, le=1)


class ReviewAdviceStore(Protocol):
    def save(self, advice: ReviewAdvice) -> None: ...

    def get(self, scope: KnowledgeScope, review_id: str) -> ReviewAdvice | None: ...

    def save_calibration(self, calibration: ReviewCalibration) -> None: ...

    def get_calibration(self, scope: KnowledgeScope) -> ReviewCalibration | None: ...

    def close(self) -> None: ...
