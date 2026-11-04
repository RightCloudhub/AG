"""Raw HotpotQA / 2WikiMultihopQA / MuSiQue → normalized records (B-02).

Raw shapes follow the official releases (and the HippoRAG 2 subset files, which keep
each dataset's native question shape plus a ``{name}_corpus.json`` passage list).
Question/dataset IDs, answer aliases, canonical passage/sentence IDs and supporting
labels are preserved; anything that cannot be mapped is reported, never guessed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.eval.public.enums import DatasetName
from agentic_graphrag.eval.public.records import (
    BenchQuestion,
    GoldLabels,
    Passage,
    normalize_text,
    passage_id_for,
)

RawRow = dict[str, Any]
_ALIAS_KEYS = ("answer_aliases", "aliases")


@dataclass
class Conversion:
    questions: list[BenchQuestion] = field(default_factory=list)
    passages: dict[str, Passage] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    def add_passage(self, passage: Passage) -> str:
        self.passages.setdefault(passage.passage_id, passage)
        return passage.passage_id


def load_raw(path: Path) -> list[RawRow]:
    """Read a JSON array or JSONL file of raw rows."""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        data = json.loads(text)
        return [dict(row) for row in data]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _answers(row: RawRow) -> tuple[str, ...]:
    answers = [str(row.get("answer") or "").strip()]
    for key in _ALIAS_KEYS:
        answers.extend(str(a).strip() for a in row.get(key) or [])
    unique = list(dict.fromkeys(a for a in answers if a))
    return tuple(unique) or ("",)


def _make_passage(dataset: DatasetName, title: str, sentences: list[str]) -> Passage:
    text = " ".join(" ".join(s.split()) for s in sentences if s.strip())
    return Passage(
        passage_id=passage_id_for(dataset, title, text),
        dataset=dataset,
        title=title,
        text=text,
        sentences=tuple(s.strip() for s in sentences),
    )


def convert_hotpot_like(rows: list[RawRow], dataset: DatasetName) -> Conversion:
    """HotpotQA and 2WikiMultihopQA share ``context`` + ``supporting_facts``."""
    conv = Conversion()
    for row in rows:
        by_title: dict[str, str] = {}
        for title, sentences in row.get("context") or []:
            pid = conv.add_passage(_make_passage(dataset, str(title), list(sentences)))
            by_title[str(title)] = pid
        qid = str(row.get("_id") or row.get("id"))
        support_p, support_s = _hotpot_support(row, by_title, qid, conv)
        conv.questions.append(
            BenchQuestion(
                dataset=dataset,
                question_id=qid,
                question=str(row["question"]),
                gold=GoldLabels(
                    answers=_answers(row),
                    supporting_passage_ids=support_p,
                    supporting_sentence_ids=support_s,
                    question_type=str(row.get("type") or ""),
                    decomposition=tuple({"evidence": e} for e in row.get("evidences") or []),
                ),
                context_passage_ids=tuple(by_title.values()),
            )
        )
    return conv


def _hotpot_support(
    row: RawRow, by_title: dict[str, str], qid: str, conv: Conversion
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    passages: list[str] = []
    sentences: list[str] = []
    for title, sent_idx in row.get("supporting_facts") or []:
        pid = by_title.get(str(title))
        if pid is None:
            conv.issues.append(f"{qid}: supporting title not in context: {title}")
            continue
        passages.append(pid)
        sentences.append(f"{pid}#s{int(sent_idx)}")
    return tuple(dict.fromkeys(passages)), tuple(dict.fromkeys(sentences))


def convert_musique(rows: list[RawRow]) -> Conversion:
    conv = Conversion()
    dataset = DatasetName.MUSIQUE
    for row in rows:
        ids: list[str] = []
        support: list[str] = []
        for para in row.get("paragraphs") or []:
            text = str(para.get("paragraph_text") or "")
            pid = conv.add_passage(_make_passage(dataset, str(para.get("title") or ""), [text]))
            ids.append(pid)
            if para.get("is_supporting"):
                support.append(pid)
        conv.questions.append(
            BenchQuestion(
                dataset=dataset,
                question_id=str(row["id"]),
                question=str(row["question"]),
                gold=GoldLabels(
                    answers=_answers(row),
                    supporting_passage_ids=tuple(dict.fromkeys(support)),
                    question_type=str(row["id"]).split("__")[0],
                    answerable=bool(row.get("answerable", True)),
                    decomposition=tuple(row.get("question_decomposition") or []),
                ),
                context_passage_ids=tuple(dict.fromkeys(ids)),
            )
        )
    return conv


def convert_rows(rows: list[RawRow], dataset: DatasetName) -> Conversion:
    if dataset is DatasetName.MUSIQUE:
        return convert_musique(rows)
    return convert_hotpot_like(rows, dataset)


def convert_dataset(
    questions_path: Path, dataset: DatasetName, *, corpus_path: Path | None = None
) -> Conversion:
    """Convert one raw file; with ``corpus_path`` the upstream corpus is canonical."""
    conv = convert_rows(load_raw(questions_path), dataset)
    if corpus_path is None:
        return conv
    from agentic_graphrag.eval.public.corpus_rekey import rekey_to_corpus

    return rekey_to_corpus(conv, load_raw(corpus_path), dataset)


def corpus_passage(dataset: DatasetName, row: RawRow) -> Passage:
    title = str(row.get("title") or "")
    text = str(row.get("text") or row.get("paragraph_text") or "").strip()
    return Passage(
        passage_id=passage_id_for(dataset, title, text),
        dataset=dataset,
        title=title,
        text=text,
    )


def title_text_key(title: str, text: str) -> tuple[str, str]:
    return normalize_text(title), normalize_text(text)
