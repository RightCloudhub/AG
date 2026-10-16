"""Deterministic / containment judge for gold answers (FR-OP-04 substrate).

LLM-as-judge is deferred (plan R6). This module exposes the same scoring
surface used by batch accuracy reports for offline eval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_graphrag.eval.scoring import score_pair


@dataclass(frozen=True)
class JudgeResult:
    correct: bool
    score: float
    method: str
    gold: str
    prediction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "score": self.score,
            "method": self.method,
            "gold": self.gold,
            "prediction": self.prediction,
        }


def judge_answer(gold: str, prediction: str) -> JudgeResult:
    """Score one prediction against gold (containment / token-overlap)."""
    row = score_pair(prediction, gold)
    return JudgeResult(
        correct=bool(row.get("correct")),
        score=float(row.get("score") or 0.0),
        method=str(row.get("method") or "containment"),
        gold=gold,
        prediction=prediction,
    )
