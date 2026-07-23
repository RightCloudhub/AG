"""Entity mention matching helpers (split from entities.py for complexity limits)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from agentic_graphrag.agent.entity_stopwords import (
    STOPWORDS,
    is_stopword_entity,
    normalize_entity_key,
)


@dataclass
class _MatchBuf:
    """Accumulates found mentions and occupied spans."""

    found: list[str] = field(default_factory=list)
    occupied: list[tuple[int, int]] = field(default_factory=list)


_QUOTED = re.compile(r"[\"“](.+?)[\"”]")
_CJK_SPAN = re.compile(r"[\u4e00-\u9fff]{2,20}")
_TITLE_SPAN = re.compile(r"\b(?:[A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})\b")


@lru_cache(maxsize=1)
def default_lexicon_from_seed() -> tuple[str, ...]:
    """Load entity surface forms from seed triples (project root)."""
    try:
        return _load_seed_lexicon()
    except Exception:
        return ()


def _load_seed_lexicon() -> tuple[str, ...]:

    from agentic_graphrag.config import resolve_path

    path = resolve_path("data/processed/seed_triples.jsonl")
    if not path.exists():
        return ()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        _collect_names_from_line(line, names)
    return tuple(sorted(names, key=lambda s: (-len(s), s.lower())))


def _collect_names_from_line(line: str, names: set[str]) -> None:
    import json

    if not line.strip():
        return
    item = json.loads(line)
    for side in ("head", "tail"):
        n = (item.get(side) or {}).get("name")
        if n and not is_stopword_entity(n):
            names.add(n.strip())


def lexicon_from_names(names: list[str] | tuple[str, ...] | None) -> list[str]:
    if not names:
        return list(default_lexicon_from_seed())
    cleaned = [n.strip() for n in names if n and not is_stopword_entity(n)]
    return sorted(set(cleaned), key=lambda s: (-len(s), s.lower()))


def extract_entity_mentions(
    text: str,
    known_entities: list[str] | tuple[str, ...] | None = None,
    *,
    max_entities: int = 5,
) -> list[str]:
    """Extract entity mentions, preferring longest known-lexicon matches."""
    if not text or not text.strip():
        return []
    lexicon = lexicon_from_names(known_entities)
    buf = _MatchBuf()
    _add_quoted(text, buf)
    _add_lexicon_hits(text, lexicon, buf)
    _add_title_spans(text, buf)
    _add_cjk_spans(text, buf)
    return _canonicalize(buf.found, lexicon, max_entities)


def _add_quoted(text: str, buf: _MatchBuf) -> None:
    for m in _QUOTED.findall(text):
        if not is_stopword_entity(m):
            buf.found.append(m.strip())


@dataclass
class _TextScan:
    text: str
    lower: str
    buf: _MatchBuf


def _add_lexicon_hits(text: str, lexicon: list[str], buf: _MatchBuf) -> None:
    scan = _TextScan(text=text, lower=text.lower(), buf=buf)
    for name in lexicon:
        _scan_lexicon_name(scan, name)


def _scan_lexicon_name(scan: _TextScan, name: str) -> None:
    key = name.lower()
    start = 0
    while True:
        idx = scan.lower.find(key, start)
        if idx < 0:
            break
        end = idx + len(key)
        if _boundary_ok(scan.text, idx, end) and not _overlaps(idx, end, scan.buf.occupied):
            scan.buf.found.append(name)
            scan.buf.occupied.append((idx, end))
        start = idx + 1


def _boundary_ok(text: str, idx: int, end: int) -> bool:
    before_ok = idx == 0 or not text[idx - 1].isalnum()
    after_ok = end >= len(text) or not text[end].isalnum()
    return before_ok and after_ok


def _add_title_spans(text: str, buf: _MatchBuf) -> None:
    for m in _TITLE_SPAN.finditer(text):
        span = _clean_title_span(m.group(0).strip())
        if span is None:
            continue
        if not _overlaps(m.start(), m.end(), buf.occupied):
            buf.found.append(span)
            buf.occupied.append((m.start(), m.end()))


def _clean_title_span(span: str) -> str | None:
    if is_stopword_entity(span):
        return None
    words = span.split()
    if words and words[0].lower() in STOPWORDS and len(words) > 1:
        span = " ".join(words[1:])
    if is_stopword_entity(span):
        return None
    if " " in span or (len(span) > 2 and span.lower() not in STOPWORDS):
        return span
    return None


def _add_cjk_spans(text: str, buf: _MatchBuf) -> None:
    for m in _CJK_SPAN.finditer(text):
        if not _overlaps(m.start(), m.end(), buf.occupied):
            buf.found.append(m.group(0))
            buf.occupied.append((m.start(), m.end()))


def _canonicalize(found: list[str], lexicon: list[str], max_entities: int) -> list[str]:
    canon_map = {normalize_entity_key(n): n for n in lexicon}
    out: list[str] = []
    seen: set[str] = set()
    for name in found:
        if is_stopword_entity(name):
            continue
        resolved = _resolve_to_lexicon(name, lexicon, canon_map)
        key = normalize_entity_key(resolved)
        if key in seen or is_stopword_entity(resolved):
            continue
        seen.add(key)
        out.append(resolved)
        if len(out) >= max_entities:
            break
    return out[:max_entities]


def _resolve_to_lexicon(name: str, lexicon: list[str], canon_map: dict[str, str]) -> str:
    """Map a surface form to a known entity (exact, then unique prefix expand).

    ``NovaTech`` → ``NovaTech Industries`` when the prefix is unambiguous.
    """
    key = normalize_entity_key(name)
    if key in canon_map:
        return canon_map[key]
    if not lexicon or not key:
        return name.strip()
    return _fuzzy_lexicon_hit(key, lexicon) or name.strip()


def _fuzzy_lexicon_hit(key: str, lexicon: list[str]) -> str | None:
    starts = [n for n in lexicon if normalize_entity_key(n).startswith(key)]
    if starts:
        return min(starts, key=lambda n: (len(n), n.lower()))
    contains = [n for n in lexicon if key in normalize_entity_key(n)]
    if len(contains) == 1:
        return contains[0]
    return None


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < b and end > a for a, b in spans)


def primary_entity(
    text: str, known_entities: list[str] | tuple[str, ...] | None = None
) -> str | None:
    mentions = extract_entity_mentions(text, known_entities, max_entities=3)
    return mentions[0] if mentions else None
