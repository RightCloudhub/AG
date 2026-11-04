"""Typed building blocks of the public benchmark run manifest (B-01, B-07, B-08, B-12)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_graphrag.eval.public import UNVERIFIED
from agentic_graphrag.eval.public.enums import (
    AG_PAIR,
    ArmKind,
    ArmName,
    DatasetName,
    ErrorKind,
    StepDownLevel,
)

STRICT = ConfigDict(extra="forbid", frozen=True)
RawFormat = Literal["hotpotqa", "2wikimultihopqa", "musique", "hipporag"]
DEFAULT_FINAL_SIZE = 1000
DEFAULT_REDUCED_SIZE = 500
DEFAULT_REPEAT_QUESTIONS = 100
DEFAULT_SPLIT_SIZES: dict[str, int] = {"spike": 50, "smoke": 3, "pilot": 50}


class ProtocolPin(BaseModel):
    """Upstream protocol identity; ``upstream_revision=None`` means UNVERIFIED."""

    model_config = STRICT
    name: str = "hipporag2"
    upstream_repo: str
    upstream_revision: str | None = None
    scorer: str = "squad_em_f1"
    verified: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _verified_needs_revision(self) -> ProtocolPin:
        if self.verified and not self.upstream_revision:
            raise ValueError("protocol.verified requires a pinned upstream_revision")
        return self

    @property
    def status(self) -> str:
        return self.upstream_revision if self.verified and self.upstream_revision else UNVERIFIED


class DatasetSpec(BaseModel):
    """One dataset: raw local artifacts (never committed), allowlist and sampling."""

    model_config = STRICT
    name: DatasetName
    raw_format: RawFormat
    final_questions_path: str
    dev_questions_path: str | None = None
    dev_raw_format: RawFormat | None = None
    corpus_path: str | None = None
    upstream_final_ids_path: str | None = None
    source_allowlist: dict[str, str | None] = Field(default_factory=dict)
    license: str = UNVERIFIED
    final_size: int = Field(default=DEFAULT_FINAL_SIZE, ge=1)
    split_sizes: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_SPLIT_SIZES))
    sampling_seed: int
    expected_passages: int | None = Field(default=None, ge=1)
    corpus_sha256: str | None = None


class ModelPin(BaseModel):
    model_config = STRICT
    id: str
    revision: str | None = None
    provider: str = ""
    supports_seed: bool = False


class ModelPins(BaseModel):
    model_config = STRICT
    generator: ModelPin
    embedding: ModelPin
    reranker: ModelPin | None = None
    extraction: ModelPin | None = None
    verifier: ModelPin | None = None


class ContextLimits(BaseModel):
    model_config = STRICT
    top_k: int = Field(default=5, ge=1)
    recall_cutoffs: tuple[int, ...] = (2, 5)
    max_context_tokens: int = Field(default=8_000, ge=1)
    max_hops: int = Field(default=5, ge=1)
    max_llm_calls_per_query: int = Field(default=20, ge=1)
    max_tokens_per_query: int = Field(default=50_000, ge=1)
    query_timeout_seconds: int = Field(default=120, ge=1)


class RetryPolicy(BaseModel):
    """Frozen retry/terminal policy: every attempt is kept; the last one is terminal."""

    model_config = STRICT
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_initial_seconds: float = Field(default=2.0, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)
    backoff_max_seconds: float = Field(default=60.0, ge=0)
    retry_on: tuple[ErrorKind, ...] = (ErrorKind.RATE_LIMIT, ErrorKind.TRANSPORT)
    terminal_selection: Literal["last_attempt"] = "last_attempt"

    def backoff(self, attempt: int) -> float:
        raw = self.backoff_initial_seconds * self.backoff_multiplier ** max(0, attempt - 1)
        return float(min(self.backoff_max_seconds, raw))


class CachePolicy(BaseModel):
    """B-08: cross-query replay caches stay off for measured trials."""

    model_config = STRICT
    answer_cache: bool = False
    retrieval_cache: bool = False
    completion_replay_cache: bool = False
    external_native_query_cache: bool = False
    within_query_dedup: bool = True
    warm_prepared_index: bool = True
    warmup_queries: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _no_replay(self) -> CachePolicy:
        flags = (
            self.answer_cache,
            self.retrieval_cache,
            self.completion_replay_cache,
            self.external_native_query_cache,
        )
        if any(flags):
            raise ValueError("cross-query replay caches must be disabled for measured trials")
        return self


class ExecutionPolicy(BaseModel):
    model_config = STRICT
    workers: int = Field(default=1, ge=1)
    block_size: int = Field(default=25, ge=1)
    order: Literal["randomized_blocks"] = "randomized_blocks"
    order_seed: int = 0
    hardware: str = UNVERIFIED


class PricePoint(BaseModel):
    """Dated price per million tokens; money is never computed without one."""

    model_config = STRICT
    model_id: str
    prompt_per_million: float = Field(ge=0)
    completion_per_million: float = Field(ge=0)
    embedding_per_million: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    as_of: date
    source: str = UNVERIFIED


class AbortRule(BaseModel):
    model_config = STRICT
    window: int = Field(default=50, ge=1)
    max_error_rate: float = Field(default=0.2, gt=0, le=1)
    error_kinds: tuple[ErrorKind, ...] = (ErrorKind.RATE_LIMIT, ErrorKind.FORBIDDEN)


class CostCeiling(BaseModel):
    """B-12 ceiling recorded before the pilot; ``None`` limits mean not yet declared."""

    model_config = STRICT
    money: float | None = Field(default=None, gt=0)
    currency: str = "USD"
    provider_tokens: int | None = Field(default=None, gt=0)
    abort: AbortRule = Field(default_factory=AbortRule)
    step_down_level: StepDownLevel = StepDownLevel.FULL
    deterministic_repeat_questions: int = Field(default=DEFAULT_REPEAT_QUESTIONS, ge=1)
    reduced_questions: int = Field(default=DEFAULT_REDUCED_SIZE, ge=1)


class ExternalSpec(BaseModel):
    """Isolated subprocess environment for an external baseline (UNVERIFIED pins)."""

    model_config = STRICT
    env_dir: str
    python: str
    driver: str = "evals/public/drivers/bench_driver.py"
    backend: str
    lock_file: str
    package: str
    package_revision: str | None = None
    native_mode: str
    native_config: dict[str, Any] = Field(default_factory=dict)
    request_timeout_seconds: float = Field(default=300.0, gt=0)


class ArmSpec(BaseModel):
    model_config = STRICT
    name: ArmName
    kind: ArmKind
    stochastic: bool = True
    register_enabled: bool | None = None
    rerank: bool = False
    external: ExternalSpec | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ArmSpec:
        if self.name in AG_PAIR:
            want = self.name is ArmName.AG_REGISTRY
            if self.kind is not ArmKind.INTERNAL_AG or self.register_enabled is not want:
                raise ValueError(f"{self.name}: internal_ag with register_enabled={want}")
        if self.kind is ArmKind.EXTERNAL and self.external is None:
            raise ValueError(f"{self.name}: external arms need an external spec")
        if (self.name is ArmName.VECTOR_RERANK) is not self.rerank:
            raise ValueError("rerank=true is reserved for the vector_rerank arm")
        return self


class PromotionBounds(BaseModel):
    """B-10 bounds; predeclared from the pilot before the final run (null = undeclared)."""

    model_config = STRICT
    min_accuracy_gain_pp: float | None = None
    max_coverage_drop_pp: float | None = None
    max_selective_risk_increase_pp: float | None = None
    max_token_ratio: float | None = None
    max_deep_p95_ms: float | None = None
    alpha: float = Field(default=0.05, gt=0, lt=1)
    declared_on: date | None = None
    declared_from: str | None = None

    @property
    def declared(self) -> bool:
        values = (
            self.min_accuracy_gain_pp,
            self.max_coverage_drop_pp,
            self.max_selective_risk_increase_pp,
            self.max_token_ratio,
        )
        return self.declared_on is not None and all(v is not None for v in values)


class SpikeRules(BaseModel):
    """B-00 exit thresholds (suggestion only; the decision field is set by a person)."""

    model_config = STRICT
    max_schema_rejection_rate: float = Field(default=0.3, ge=0, le=1)
    min_graph_coverage: float = Field(default=0.6, ge=0, le=1)
    min_f1_delta_vs_vector: float = -0.05
