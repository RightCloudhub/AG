"""Ingestion configuration; document parsing stays offline unless opted in."""

from __future__ import annotations

import codecs
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_graphrag.config_review import ReviewAutomationConfig

DEFAULT_CHUNK_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 64
DEFAULT_TOKENIZER = "cl100k_base"


class MinerUConfig(BaseModel):
    enabled: bool = False
    endpoint: str = ""
    timeout_seconds: float = Field(default=120.0, gt=0)


class ParsingConfig(BaseModel):
    mineru: MinerUConfig = Field(default_factory=MinerUConfig)
    encoding_min_confidence: float = Field(default=0.8, gt=0, le=1)
    encoding_preference: str = "gb18030"

    @field_validator("encoding_preference")
    @classmethod
    def known_encoding(cls, value: str) -> str:
        try:
            return codecs.lookup(value).name
        except LookupError as exc:
            raise ValueError("encoding_preference must name a Python text codec") from exc


class KnowledgeConfig(BaseModel):
    # Reject obsolete *_chars settings instead of silently ignoring their units.
    model_config = ConfigDict(extra="forbid")
    agent_review: ReviewAutomationConfig = Field(default_factory=ReviewAutomationConfig)

    chunk_size_tokens: int = Field(default=DEFAULT_CHUNK_TOKENS, gt=0)
    chunk_overlap_tokens: int = Field(default=DEFAULT_OVERLAP_TOKENS, ge=0)
    tokenizer: str = DEFAULT_TOKENIZER
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    extract_confidence_threshold: float = 0.5
    schema_path: str = "configs/schema/domain_v0.yaml"
    extract_max_attempts: int = 3
    extract_retry_base_delay_seconds: float = 0.5
    extract_journal_path: str = "data/processed/extract_journal.jsonl"
    extract_quarantine_path: str = "data/processed/extract_quarantine.jsonl"

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
        return self
