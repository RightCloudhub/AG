"""Run manifest (B-01): one frozen, hashable description of a benchmark run."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from agentic_graphrag.eval.public.enums import (
    REQUIRED_ARMS,
    REQUIRED_DATASETS,
    ArmName,
    DatasetName,
    RunKind,
    SplitName,
)
from agentic_graphrag.eval.public.manifest_models import (
    STRICT,
    ArmSpec,
    CachePolicy,
    ContextLimits,
    CostCeiling,
    DatasetSpec,
    ExecutionPolicy,
    ModelPins,
    PricePoint,
    PromotionBounds,
    ProtocolPin,
    RetryPolicy,
    SpikeRules,
)

MIN_FINAL_SEEDS = 3
DEFAULT_BOOTSTRAP_RESAMPLES = 2000


class RunManifest(BaseModel):
    model_config = STRICT
    manifest_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    run_kind: RunKind
    split: SplitName
    created: date
    protocol: ProtocolPin
    datasets: tuple[DatasetSpec, ...]
    arms: tuple[ArmSpec, ...]
    run_seeds: tuple[int, ...]
    questions_per_dataset: int = Field(ge=1)
    models: ModelPins
    context: ContextLimits = Field(default_factory=ContextLimits)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    cache: CachePolicy = Field(default_factory=CachePolicy)
    execution: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    prices: tuple[PricePoint, ...] = ()
    ceiling: CostCeiling = Field(default_factory=CostCeiling)
    promotion: PromotionBounds = Field(default_factory=PromotionBounds)
    spike_rules: SpikeRules | None = None
    workspace_dir: str
    results_dir: str
    store_namespace: str = Field(pattern=r"^bench_[a-z0-9_]+$")
    extraction_schema_path: str = "configs/schema/open_domain_bench_v0.yaml"
    reader_prompt_path: str = "configs/benchmarks/prompts/reader.md"
    abstention_detector: str = "abstain-v1"
    bootstrap_resamples: int = Field(default=DEFAULT_BOOTSTRAP_RESAMPLES, ge=10)
    bootstrap_seed: int = 0

    @model_validator(mode="after")
    def _check(self) -> RunManifest:
        _require_unique([a.name for a in self.arms], "arm")
        _require_unique([d.name for d in self.datasets], "dataset")
        _require_unique(list(self.run_seeds), "run seed")
        if not self.run_seeds:
            raise ValueError("at least one run seed is required")
        clash = set(self.run_seeds) & {d.sampling_seed for d in self.datasets}
        if clash:
            raise ValueError(f"run seeds must differ from sampling seeds: {sorted(clash)}")
        _check_split(self)
        if self.run_kind is RunKind.FINAL:
            _check_final(self)
        return self

    def arm(self, name: ArmName) -> ArmSpec:
        for spec in self.arms:
            if spec.name is name:
                return spec
        raise KeyError(name)

    def dataset(self, name: DatasetName) -> DatasetSpec:
        for spec in self.datasets:
            if spec.name is name:
                return spec
        raise KeyError(name)

    def price_for(self, model_id: str) -> PricePoint | None:
        return next((p for p in self.prices if p.model_id == model_id), None)


def _require_unique(values: list[Any], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} in manifest")


def _check_split(m: RunManifest) -> None:
    if (m.run_kind is RunKind.FINAL) != (m.split is SplitName.FINAL):
        raise ValueError("only final runs may use the final split (and they must)")


def _check_final(m: RunManifest) -> None:
    missing_arms = set(REQUIRED_ARMS) - {a.name for a in m.arms}
    missing_sets = set(REQUIRED_DATASETS) - {d.name for d in m.datasets}
    if missing_arms or missing_sets:
        raise ValueError(
            f"final runs need all arms/datasets; missing {sorted(missing_arms | missing_sets)}"
        )
    if len(m.run_seeds) < MIN_FINAL_SEEDS:
        raise ValueError(f"final runs need >= {MIN_FINAL_SEEDS} run seeds")


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_hash(manifest: RunManifest) -> str:
    """sha256 of the canonical JSON dump; any field change yields a new hash."""
    payload = canonical_json(manifest.model_dump(mode="json"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_manifest(path: str | Path) -> RunManifest:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest {path} must be a mapping")
    return RunManifest.model_validate(data)


def manifest_summary(manifest: RunManifest) -> dict[str, Any]:
    return {
        "run_id": manifest.run_id,
        "run_kind": manifest.run_kind.value,
        "manifest_sha256": manifest_hash(manifest),
        "protocol_revision": manifest.protocol.status,
        "step_down_level": int(manifest.ceiling.step_down_level),
        "arms": [a.name.value for a in manifest.arms],
        "datasets": [d.name.value for d in manifest.datasets],
        "run_seeds": list(manifest.run_seeds),
    }
