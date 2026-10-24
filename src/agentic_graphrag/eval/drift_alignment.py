"""Text-only gold-passage alignment; unmatched evidence stays explicitly unknown."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from agentic_graphrag.core.json_types import JsonObject

FINGERPRINT_CHARS = 40
FUZZY_THRESHOLD = 0.9


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def align_passages(
    passages: list[str],
    chunks: list[JsonObject],
    *,
    fingerprint_chars: int = FINGERPRINT_CHARS,
    fuzzy_threshold: float = FUZZY_THRESHOLD,
) -> JsonObject:
    """Exact fingerprint first, fuzzy window second; never invent chunk mappings."""
    if fingerprint_chars < 1 or not 0 < fuzzy_threshold <= 1:
        raise ValueError("invalid alignment settings")
    corpus = [(str(c["id"]), normalize_text(str(c["content"]))) for c in chunks]
    mappings = [
        _align_one(
            index, normalize_text(p)[:fingerprint_chars], corpus, fuzzy_threshold=fuzzy_threshold
        )
        for index, p in enumerate(passages)
    ]
    unmatched = [m["passage_index"] for m in mappings if not m["chunk_ids"]]
    return {
        "mappings": mappings,
        "unmatched": unmatched,
        "coverage": (len(passages) - len(unmatched)) / len(passages) if passages else None,
    }


def _align_one(
    index: int, fingerprint: str, corpus: list[tuple[str, str]], *, fuzzy_threshold: float
) -> JsonObject:
    ids = [rid for rid, text in corpus if fingerprint and fingerprint in text]
    method = "fingerprint"
    if not ids and fingerprint:
        ids = [rid for rid, text in corpus if _window_match(fingerprint, text) >= fuzzy_threshold]
        method = "fuzzy"
    return {"passage_index": index, "chunk_ids": ids, "method": method if ids else "unmatched"}


def _window_match(fingerprint: str, text: str) -> float:
    matcher = SequenceMatcher(None, fingerprint, text, autojunk=False)
    block = matcher.find_longest_match()
    start = max(0, block.b - block.a)
    window = text[start : start + len(fingerprint)]
    return SequenceMatcher(None, fingerprint, window, autojunk=False).ratio()
