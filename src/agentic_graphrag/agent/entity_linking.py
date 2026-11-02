"""Graph-degree prior for ambiguous short entity mentions.

``extract_entity_mentions`` refuses to expand an ambiguous prefix ("NovaTech"
→ NovaTech Industries vs NovaTech Edge Labs Spin-in) and passes the bare
surface form on, which matches no graph node, so retrieval returns nothing.
A standard entity-linking prior resolves it: prefer the candidate that is far
better connected in the graph, and only when it clearly dominates.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

from agentic_graphrag.config_matching import question_matching_config
from agentic_graphrag.core.ports.stores import GraphStore
from agentic_graphrag.core.text_normalization import normalize_text

# The best candidate must have at least this many times the runner-up's degree.
_DOMINANCE = 2
_DEGREE_PROBE_LIMIT = 100
_WORD = re.compile(r"[a-z0-9]+(?:[&.\-][a-z0-9]+)*", re.I)


def match_entity_spelling(name: str, known: list[str]) -> str | None:
    """Require a strong full-name match and a clear margin over the runner-up."""
    return _match_spelling(normalize_text(name).casefold(), tuple(known))


@lru_cache(maxsize=4096)
def _match_spelling(key: str, known: tuple[str, ...]) -> str | None:
    cfg = question_matching_config()
    if len(key) < cfg.entity_min_chars:
        return None
    ranked = sorted(((_similarity(key, k), n) for k, n in _name_keys(known)), reverse=True)
    if not ranked or ranked[0][0] < cfg.entity_similarity_threshold:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < cfg.entity_similarity_margin:
        return None
    return ranked[0][1]


@lru_cache(maxsize=64)
def _name_keys(known: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((normalize_text(n).casefold(), n) for n in known)


def _similarity(a: str, b: str) -> float:
    # Full names only: avoid turning short prefixes or partial brands into guesses.
    if abs(len(a) - len(b)) > 2 or len(a.split()) != len(b.split()) or (b in a and a != b):
        return 0.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    cfg = question_matching_config()
    # quick_ratio is an upper bound. Values below this cannot win or affect the margin.
    if matcher.quick_ratio() < cfg.entity_similarity_threshold - cfg.entity_similarity_margin:
        return 0.0
    return matcher.ratio()


@lru_cache(maxsize=64)
def _known_patterns(known: tuple[str, ...]) -> re.Pattern[str]:
    names = sorted({normalize_text(n) for n in known if n}, key=lambda n: (-len(n), n))
    body = "|".join(_bounded_name(n) for n in names) or r"(?!)"
    return re.compile(body, re.I)


def _bounded_name(name: str) -> str:
    # Latin names can abut Chinese grammatical particles; Chinese names need word boundaries.
    left = "[a-z0-9]" if name[0].isascii() else r"\w"
    right = "[a-z0-9]" if name[-1].isascii() else r"\w"
    return rf"(?<!{left}){re.escape(name)}(?!{right})"


def entity_spans(text: str, known: tuple[str, ...]) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _known_patterns(known).finditer(text)]


@lru_cache(maxsize=2048)
def repair_entity_mentions(text: str, known: tuple[str, ...]) -> str:
    """Correct unambiguous full-name typos, protecting exact mentions first."""
    if not known:
        return text
    occupied = entity_spans(text, known)
    words = list(_WORD.finditer(text))
    by_size: dict[int, list[str]] = {}
    for name in known:
        by_size.setdefault(len(name.split()), []).append(name)
    replacements: list[tuple[int, int, str]] = []
    for size in sorted(by_size, reverse=True):
        _repair_windows(
            text,
            words,
            candidates=by_size[size],
            size=size,
            occupied=occupied,
            replacements=replacements,
        )
    for start, end, name in sorted(replacements, reverse=True):
        text = text[:start] + name + text[end:]
    return text


def _repair_windows(
    text: str,
    words: list[re.Match[str]],
    *,
    candidates: list[str],
    size: int,
    occupied: list[tuple[int, int]],
    replacements: list[tuple[int, int, str]],
) -> None:
    for offset in range(len(words) - size + 1):
        start, end = words[offset].start(), words[offset + size - 1].end()
        if any(start < b and end > a for a, b in occupied):
            continue
        phrase = text[start:end]
        if " ".join(_WORD.findall(phrase)).casefold() != phrase.casefold():
            continue
        match = match_entity_spelling(phrase, candidates)
        if match:
            occupied.append((start, end))
            replacements.append((start, end, match))


def link_ambiguous_mention(
    name: str, known: list[str], store: GraphStore, *, tenant_id: str | None = None
) -> str:
    """Expand ``name`` to the dominant known entity it prefixes, else keep it."""
    key = name.strip().lower()
    if not key or any(k.lower() == key for k in known):
        return name
    candidates = [k for k in known if k.lower().startswith(key + " ")]
    if len(candidates) < 2:
        return name
    ranked = sorted(
        ((_degree(store, c, tenant_id=tenant_id), c) for c in candidates),
        key=lambda t: (-t[0], t[1]),
    )
    best, runner_up = ranked[0], ranked[1]
    if best[0] > 0 and best[0] >= _DOMINANCE * max(1, runner_up[0]):
        return best[1]
    return name


def _degree(store: GraphStore, name: str, *, tenant_id: str | None = None) -> int:
    try:
        return len(
            store.neighbors(name, max_hops=1, limit=_DEGREE_PROBE_LIMIT, tenant_id=tenant_id)
        )
    except Exception:  # noqa: BLE001 — a missing node simply has no prior
        return 0
