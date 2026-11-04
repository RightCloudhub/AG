"""Re-key question support labels onto an upstream corpus file (B-02).

HippoRAG 2 subsets ship the retrieval corpus separately. Its passages are canonical;
question-context passages map onto them by (title, normalized text) first, then by a
title that is unique in the corpus. Unmapped labels are reported, never guessed.
"""

from __future__ import annotations

from collections import defaultdict

from agentic_graphrag.eval.public.converters import (
    Conversion,
    RawRow,
    corpus_passage,
    title_text_key,
)
from agentic_graphrag.eval.public.enums import DatasetName
from agentic_graphrag.eval.public.records import BenchQuestion, GoldLabels, Passage, normalize_text


class _CorpusIndex:
    def __init__(self, passages: list[Passage]) -> None:
        self.by_key: dict[tuple[str, str], str] = {}
        titles: dict[str, list[str]] = defaultdict(list)
        for p in passages:
            self.by_key.setdefault(title_text_key(p.title, p.text), p.passage_id)
            titles[normalize_text(p.title)].append(p.passage_id)
        self.unique_title = {t: ids[0] for t, ids in titles.items() if len(set(ids)) == 1}

    def resolve(self, passage: Passage) -> str | None:
        exact = self.by_key.get(title_text_key(passage.title, passage.text))
        return exact or self.unique_title.get(normalize_text(passage.title))


def rekey_to_corpus(conv: Conversion, corpus_rows: list[RawRow], dataset: DatasetName) -> Conversion:
    corpus = [corpus_passage(dataset, row) for row in corpus_rows]
    index = _CorpusIndex(corpus)
    mapping: dict[str, str] = {}
    for old_id, passage in conv.passages.items():
        new_id = index.resolve(passage)
        if new_id is not None:
            mapping[old_id] = new_id
    out = Conversion(issues=list(conv.issues))
    for passage in corpus:
        out.add_passage(passage)
    for question in conv.questions:
        out.questions.append(_rekey_question(question, mapping, out.issues))
    return out


def _rekey_question(q: BenchQuestion, mapping: dict[str, str], issues: list[str]) -> BenchQuestion:
    missing = [pid for pid in q.gold.supporting_passage_ids if pid not in mapping]
    if missing:
        issues.append(f"{q.question_id}: {len(missing)} supporting passage(s) not in corpus")
    gold = GoldLabels(
        answers=q.gold.answers,
        supporting_passage_ids=_mapped(q.gold.supporting_passage_ids, mapping),
        supporting_sentence_ids=_mapped_sentences(q.gold.supporting_sentence_ids, mapping),
        question_type=q.gold.question_type,
        answerable=q.gold.answerable,
        decomposition=q.gold.decomposition,
    )
    return q.model_copy(
        update={"gold": gold, "context_passage_ids": _mapped(q.context_passage_ids, mapping)}
    )


def _mapped(ids: tuple[str, ...], mapping: dict[str, str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(mapping[i] for i in ids if i in mapping))


def _mapped_sentences(ids: tuple[str, ...], mapping: dict[str, str]) -> tuple[str, ...]:
    out: list[str] = []
    for sid in ids:
        pid, _, suffix = sid.partition("#")
        if pid in mapping:
            out.append(f"{mapping[pid]}#{suffix}")
    return tuple(dict.fromkeys(out))
