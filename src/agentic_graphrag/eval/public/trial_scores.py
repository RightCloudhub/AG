"""Per-trial scoring: terminal result → answer metrics, evidence recall, cost (B-09)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from agentic_graphrag.eval.public.accounting import money
from agentic_graphrag.eval.public.enums import ArmName, DatasetName, TrialStatus
from agentic_graphrag.eval.public.manifest_models import PricePoint
from agentic_graphrag.eval.public.records import BenchQuestion
from agentic_graphrag.eval.public.scoring import (
    AbstentionDetector,
    contain_match,
    exact_match,
    f1_score,
    recall_at,
)
from agentic_graphrag.eval.public.trials import TrialState

NATIVE_ABSTAIN = frozenset({"partial", "no_answer"})
RETRIEVED_ALL = "retrieved_all"
CONTEXT = "context"
CITED = "cited"


def recall_keys(cutoffs: Sequence[int]) -> tuple[str, ...]:
    return (*(f"retrieved@{k}" for k in cutoffs), RETRIEVED_ALL, CONTEXT, CITED)


class ScoredTrial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arm: ArmName
    dataset: DatasetName
    run_seed: int
    question_id: str
    status: TrialStatus
    em: float
    f1: float
    contain: float
    abstained: bool
    native_status: str | None = None
    confidence: float | None = None
    recall: dict[str, float | None]
    latency_ms: float | None = None
    trial_elapsed_ms: float | None = None
    tokens: int | None = None
    llm_calls: int | None = None
    money: float | None = None
    attempts: int
    interrupted_attempts: int
    alignment_coverage: dict[str, float | None]

    @property
    def native_abstained(self) -> bool | None:
        return None if self.native_status is None else self.native_status in NATIVE_ABSTAIN


def _recall(
    gold: Sequence[str],
    ids: Sequence[str] | None,
    cutoffs: Sequence[int],
    context: Sequence[str] | None,
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for k in cutoffs:
        out[f"retrieved@{k}"] = None if ids is None else recall_at(gold, ids, k)
    out[RETRIEVED_ALL] = None if ids is None else recall_at(gold, ids)
    out[CONTEXT] = None if context is None else recall_at(gold, context)
    return out


def score_state(
    state: TrialState,
    question: BenchQuestion,
    *,
    detector: AbstentionDetector,
    cutoffs: Sequence[int],
    price: PricePoint | None,
) -> ScoredTrial:
    terminal = state.terminal
    if terminal is None:
        raise ValueError(f"{state.key.id}: trial is not finished")
    gold = question.gold.supporting_passage_ids
    answers = question.gold.answers
    recall = _recall(gold, terminal.retrieved_passage_ids, cutoffs, terminal.context_passage_ids)
    cited = terminal.cited_passage_ids
    recall[CITED] = None if cited is None else recall_at(gold, cited)
    usage = state.usage()
    return ScoredTrial(
        arm=state.key.arm,
        dataset=state.key.dataset,
        run_seed=state.key.run_seed,
        question_id=state.key.question_id,
        status=terminal.status,
        em=exact_match(terminal.prediction, answers),
        f1=f1_score(terminal.prediction, answers),
        contain=contain_match(terminal.prediction, answers),
        abstained=detector.abstained(terminal.prediction),
        native_status=terminal.native_status,
        confidence=terminal.confidence,
        recall=recall,
        latency_ms=terminal.usage.elapsed_ms,
        trial_elapsed_ms=usage.elapsed_ms,
        tokens=usage.total_tokens,
        llm_calls=usage.llm_calls,
        money=money(usage, price),
        attempts=len(state.attempt_starts),
        interrupted_attempts=len(state.interrupted),
        alignment_coverage={k: v.coverage for k, v in terminal.alignment.items()},
    )


def score_states(
    states: Mapping[str, TrialState],
    questions: Mapping[tuple[DatasetName, str], BenchQuestion],
    *,
    detector: AbstentionDetector,
    cutoffs: Sequence[int],
    price: PricePoint | None,
    planned: set[str] | None = None,
) -> list[ScoredTrial]:
    """Score finished, planned trials only; unfinished ones are reported as missing."""
    out: list[ScoredTrial] = []
    for kid, state in states.items():
        if not state.done or (planned is not None and kid not in planned):
            continue
        question = questions[(state.key.dataset, state.key.question_id)]
        out.append(score_state(state, question, detector=detector, cutoffs=cutoffs, price=price))
    return out
