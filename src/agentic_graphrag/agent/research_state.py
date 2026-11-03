"""A researcher receives one requirement, never the shared register."""

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.agent.blackboard import BranchEvent, BudgetEntry, Fact, Proposal
from agentic_graphrag.agent.requirements import Requirement
from agentic_graphrag.agent.research_cache import QueryCache
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ToolCallTrace


class ResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str
    run_id: str
    tenant_id: str
    question: str
    requirement: Requirement
    round: int
    branch_count: int = 1
    known_evidence_ids: list[str] = Field(default_factory=list)
    token_limit: int
    call_limit: int
    tool_limit: int


class ResearchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement: Requirement
    facts: list[Fact] = Field(default_factory=list)
    next_query: str = ""


class ResearchOutput(TypedDict, total=False):
    evidence: dict[str, Candidate]
    proposals: list[Proposal]
    events: list[BranchEvent]
    budget_ledger: list[BudgetEntry]
    query_cache: QueryCache


class ResearchState(ResearchOutput, total=False):
    state_version: int
    task: ResearchTask
    query: str
    decision: ResearchDecision
    traces: list[ToolCallTrace]
    receipts: list[BudgetEntry]
    calls: int
    tools_used: int
    outcome: str


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    entailed: bool
    reason: str


class VerificationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[VerificationResult]
