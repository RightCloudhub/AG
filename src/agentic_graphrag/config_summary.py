"""Bounded background summary and global-query execution settings."""

from pydantic import BaseModel, Field


class SummaryConfig(BaseModel):
    index_max_llm_calls: int = Field(default=10000, ge=1, le=100000)
    index_max_tokens: int = Field(default=50000000, ge=1000, le=100000000)
    source_tokens_per_call: int = Field(default=4000, ge=256, le=32000)
    max_findings_per_call: int = Field(default=20, ge=1, le=100)
    query_max_llm_calls: int = Field(default=120, ge=1, le=1000)
    query_max_tokens: int = Field(default=200000, ge=1000, le=2000000)
    max_map_units: int = Field(default=500, ge=1, le=10000)
    reduce_batch_size: int = Field(default=20, ge=2, le=100)
    community_seed: int = 0
    community_resolution: float = Field(default=1.0, gt=0)
    incremental_full_rebuild_ratio: float = Field(default=0.75, gt=0, le=1)
    serious_finding_threshold: float = Field(default=0.85, ge=0, le=1)
