"""Answer scoring, the shared abstention detector and evidence recall (B-09).

Metric names are explicit: ``em_squad`` / ``f1_squad`` use the official SQuAD v1.1
normalization (lowercase, strip punctuation and articles, collapse whitespace) and
take the max over gold aliases; ``contain_normalized`` is the looser contain-match.
"""

from __future__ import annotations

import hashlib
import re
import string
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from agentic_graphrag.eval.search_drift import ABSTENTION as SEARCH_DRIFT_ABSTENTION

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = frozenset(string.punctuation)
_ANSWER_PREFIX = re.compile(r"answer\s*[:：]\s*", re.I)
EM_METRIC = "em_squad"
F1_METRIC = "f1_squad"
CONTAIN_METRIC = "contain_normalized"


def normalize_answer(text: str) -> str:
    lowered = text.lower()
    no_punct = "".join(ch for ch in lowered if ch not in _PUNCT)
    return " ".join(_ARTICLES.sub(" ", no_punct).split())


def answer_span(prediction: str) -> str:
    """Text after the last ``Answer:`` marker (first line), else the stripped prediction.

    Applied identically to every arm before scoring.
    """
    matches = list(_ANSWER_PREFIX.finditer(prediction))
    if not matches:
        return prediction.strip()
    tail = prediction[matches[-1].end() :]
    return tail.strip().splitlines()[0].strip() if tail.strip() else ""


def exact_match(prediction: str, golds: Sequence[str]) -> float:
    pred = normalize_answer(answer_span(prediction))
    return float(any(pred == normalize_answer(g) for g in golds))


def _f1_single(pred: str, gold: str) -> float:
    p_tokens = normalize_answer(pred).split()
    g_tokens = normalize_answer(gold).split()
    common = Counter(p_tokens) & Counter(g_tokens)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(p_tokens)
    recall = overlap / len(g_tokens)
    return 2 * precision * recall / (precision + recall)


def f1_score(prediction: str, golds: Sequence[str]) -> float:
    span = answer_span(prediction)
    return max((_f1_single(span, g) for g in golds), default=0.0)


def contain_match(prediction: str, golds: Sequence[str]) -> float:
    pred = normalize_answer(prediction)
    return float(any(normalize_answer(g) and normalize_answer(g) in pred for g in golds))


# One frozen detector shared by every arm: the search-drift split plus English variants
# common in public QA outputs. Changing it requires a new version id.
_EXTRA_ABSTENTION = (
    r"\b(?:cannot|can't|unable to) (?:be )?(?:determine|answer|find|tell)"
    r"|\b(?:insufficient|no) (?:evidence|information)\b"
    r"|\bi (?:do not|don't) know\b|\bnot (?:mentioned|provided|stated) in the passages\b"
)
ABSTENTION_PATTERN = f"(?:{SEARCH_DRIFT_ABSTENTION.pattern})|{_EXTRA_ABSTENTION}"


@dataclass(frozen=True)
class AbstentionDetector:
    version: str
    pattern: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pattern.encode("utf-8")).hexdigest()

    def abstained(self, prediction: str) -> bool:
        """Final answer text only; empty output counts as abstention."""
        text = prediction.strip()
        return not text or re.search(self.pattern, text, re.I) is not None


DETECTORS: dict[str, AbstentionDetector] = {
    "abstain-v1": AbstentionDetector(version="abstain-v1", pattern=ABSTENTION_PATTERN),
}


def detector_for(version: str) -> AbstentionDetector:
    try:
        return DETECTORS[version]
    except KeyError as exc:
        raise ValueError(f"unknown abstention detector {version!r}") from exc


def recall_at(gold: Iterable[str], ranked: Sequence[str], k: int | None = None) -> float | None:
    """Share of gold passages within the first ``k`` ranked ids (all when ``k`` is None)."""
    gold_set = set(gold)
    if not gold_set:
        return None
    window = ranked if k is None else ranked[:k]
    return len(gold_set & set(window)) / len(gold_set)
