"""Typed scoring records shared by evaluation and report consumers."""

from typing import NotRequired, TypedDict


class PairScore(TypedDict):
    correct: bool
    score: float
    method: str
    overlap: NotRequired[list[str]]


class ScoredCase(PairScore):
    case_id: str | None
    gold: str | None
    prediction: str


class PredictionRow(TypedDict, total=False):
    case_id: str
    gold: str
    prediction: str
    error: str


class AccuracySummary(TypedDict):
    total: int
    correct: int
    accuracy: float
    accuracy_pct: float
    cases: list[ScoredCase]
