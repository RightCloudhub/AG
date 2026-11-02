"""Opt-in second-stage retrieval controls; offline wiring always disables them."""

from typing import Literal

from pydantic import BaseModel, Field

from agentic_graphrag.core.enums import RerankKind


class RerankConfig(BaseModel):
    kind: RerankKind = RerankKind.NONE
    candidate_pool: int = Field(default=30, ge=1)
    top_n: int | None = Field(default=None, ge=1)  # legacy override of retrieval.top_k
    endpoint: str = "http://127.0.0.1:8081/rerank"
    model: str = "BAAI/bge-reranker-v2-m3"
    timeout_seconds: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    batch_size: int = Field(default=16, ge=1)
    max_chars: int = Field(default=2000, ge=1)
    max_query_chars: int = Field(default=1000, ge=1)
    max_length: int = Field(default=512, ge=2)  # local tokenizer's pair limit
    graph_policy: Literal["score", "exempt", "floor"] = "exempt"
    graph_floor: float = Field(default=0.0, allow_inf_nan=False)
    failure_threshold: int = Field(default=2, ge=1)
    cooldown_seconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)
