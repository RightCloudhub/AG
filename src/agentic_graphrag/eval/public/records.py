"""Normalized public-benchmark records (B-02).

Adapters only ever receive :class:`QuestionInput` and :class:`Passage` text. Gold
answers, supporting labels and decompositions live in :class:`GoldLabels` and stay
evaluator-only (B-03).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.eval.public.enums import DatasetName

PASSAGE_DIGEST_CHARS = 16
_FROZEN = ConfigDict(extra="forbid", frozen=True)


class Passage(BaseModel):
    """One canonical corpus passage; the only data that may reach indexing."""

    model_config = _FROZEN
    passage_id: str
    dataset: DatasetName
    title: str
    text: str
    sentences: tuple[str, ...] = ()

    def index_text(self) -> str:
        return f"{self.title}\n{self.text}" if self.title else self.text

    def sentence_id(self, index: int) -> str:
        return f"{self.passage_id}#s{index}"


class GoldLabels(BaseModel):
    model_config = _FROZEN
    answers: tuple[str, ...] = Field(min_length=1)
    supporting_passage_ids: tuple[str, ...] = ()
    supporting_sentence_ids: tuple[str, ...] = ()
    question_type: str = ""
    answerable: bool = True
    decomposition: tuple[dict[str, Any], ...] = ()


class BenchQuestion(BaseModel):
    model_config = _FROZEN
    dataset: DatasetName
    question_id: str
    question: str
    gold: GoldLabels
    context_passage_ids: tuple[str, ...] = ()

    def to_input(self) -> QuestionInput:
        return QuestionInput(
            dataset=self.dataset, question_id=self.question_id, question=self.question
        )


class QuestionInput(BaseModel):
    """What a system under test may see: no answers, labels or decompositions."""

    model_config = _FROZEN
    dataset: DatasetName
    question_id: str
    question: str


def passage_id_for(dataset: DatasetName, title: str, text: str) -> str:
    """Content-addressed canonical id: identical passages dedupe across questions."""
    digest = hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()
    return f"{dataset.value}:{digest[:PASSAGE_DIGEST_CHARS]}"


def normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def text_key(text: str) -> str:
    """Whitespace/case-insensitive content key used for deterministic text alignment."""
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()


def write_jsonl(path: Path, rows: Iterable[BaseModel]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.model_dump(mode="json"), ensure_ascii=False) + "\n")
            count += 1
    return count


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def read_models[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    return [model.model_validate(row) for row in iter_jsonl(path)]
