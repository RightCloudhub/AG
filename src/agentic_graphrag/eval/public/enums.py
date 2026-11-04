"""Closed vocabularies shared by the public benchmark runner."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class DatasetName(StrEnum):
    HOTPOTQA = "hotpotqa"
    TWOWIKI = "2wikimultihopqa"
    MUSIQUE = "musique"


class ArmName(StrEnum):
    PURE_VECTOR = "pure_vector"
    VECTOR_RERANK = "vector_rerank"
    LIGHTRAG = "lightrag"
    HIPPORAG2 = "hipporag2"
    AG_SINGLE_TIER = "ag_single_tier"
    AG_REGISTRY = "ag_registry"


class ArmKind(StrEnum):
    INTERNAL_VECTOR = "internal_vector"
    INTERNAL_AG = "internal_ag"
    EXTERNAL = "external"


class RunKind(StrEnum):
    """Run ladder rows (§4.4); only ``final`` feeds public claims."""

    FIXTURE = "fixture"
    SPIKE = "spike"
    SMOKE = "smoke"
    PILOT = "pilot"
    FINAL = "final"


class SplitName(StrEnum):
    DEV = "dev"
    SPIKE = "spike"
    SMOKE = "smoke"
    PILOT = "pilot"
    FINAL = "final"


class TrialStatus(StrEnum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    NO_ANSWER = "no_answer"
    ERROR = "error"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"


class ErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    FORBIDDEN = "forbidden"
    TRANSPORT = "transport"
    BUDGET = "budget"
    PROTOCOL = "protocol"
    INTERRUPTED = "interrupted"
    OTHER = "other"


class StepDownLevel(IntEnum):
    """B-12 predeclared fallback scale, applied strictly in this order."""

    FULL = 0
    AG_AND_STOCHASTIC_SEEDS = 1
    ONE_SEED_NON_AG = 2
    HALF_QUESTIONS = 3


AG_PAIR: frozenset[ArmName] = frozenset({ArmName.AG_SINGLE_TIER, ArmName.AG_REGISTRY})
GRAPH_BUILDING_ARMS: frozenset[ArmName] = frozenset(
    {ArmName.AG_SINGLE_TIER, ArmName.LIGHTRAG, ArmName.HIPPORAG2}
)
REQUIRED_ARMS: tuple[ArmName, ...] = tuple(ArmName)
REQUIRED_DATASETS: tuple[DatasetName, ...] = tuple(DatasetName)
FAILED_STATUSES: frozenset[TrialStatus] = frozenset(
    {TrialStatus.ERROR, TrialStatus.TIMEOUT, TrialStatus.INTERRUPTED}
)
