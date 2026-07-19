"""Entity mention extraction for offline / heuristic tool selection.

Prefers longest matches against a known entity lexicon (from the graph/corpus)
and filters interrogative stopwords so graph tools never target "Who"/"Which".
"""

from __future__ import annotations

import re
from functools import lru_cache

# Interrogatives / function words that look Capitalized at sentence start
_STOPWORDS = frozenset(
    {
        "who",
        "what",
        "which",
        "where",
        "when",
        "why",
        "how",
        "whom",
        "whose",
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "both",
        "did",
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
        "among",
        "find",
        "list",
        "name",
        "show",
        "tell",
        "also",
        "with",
        "from",
        "into",
        "about",
        "this",
        "that",
        "these",
        "those",
        "their",
        "its",
        "his",
        "her",
        "our",
        "your",
        "ceo",  # role word, not an entity id for graph expand
        "parent",
        "subsidiary",
        "company",
        "companies",
        "product",
        "products",
        "supplier",
        "suppliers",
        "event",
        "previously",
        "currently",
        "named",
        "called",
        "owns",
        "owned",
        "produce",
        "produces",
        "producer",
        "work",
        "worked",
        "works",
        "lead",
        "leads",
        "led",
        "now",
        "before",
        "after",
        "shared",
        "business",
        "connections",
        "relationship",
        "chain",
        "executives",
        "corpus",
        "firm",
        "firms",
    }
)

_QUOTED = re.compile(r"[\"“](.+?)[\"”]")
_CJK_SPAN = re.compile(r"[\u4e00-\u9fff]{2,20}")
_CAP_WORD = re.compile(r"\b[A-Z][A-Za-z0-9&.-]*\b")
# Multi-word Title Case spans: "NovaTech Industries", "Apex Holdings"
_TITLE_SPAN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})\b"
)


def is_stopword_entity(name: str) -> bool:
    key = name.strip().lower()
    if not key or key in _STOPWORDS:
        return True
    # Reject pure role/function single tokens
    if " " not in key and key in _STOPWORDS:
        return True
    return False


def normalize_entity_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


@lru_cache(maxsize=1)
def default_lexicon_from_seed() -> tuple[str, ...]:
    """Load entity surface forms from seed triples (project root)."""
    try:
        import json

        from agentic_graphrag.config import resolve_path

        path = resolve_path("data/processed/seed_triples.jsonl")
        if not path.exists():
            return ()
        names: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            for side in ("head", "tail"):
                n = (item.get(side) or {}).get("name")
                if n and not is_stopword_entity(n):
                    names.add(n.strip())
        # Longest first for matching
        return tuple(sorted(names, key=lambda s: (-len(s), s.lower())))
    except Exception:
        return ()


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
    """Extract entity mentions, preferring longest known-lexicon matches.

    Never returns interrogative stopwords (Who/Which/What) as sole entities
    when a named corpus entity is present in the question.
    """
    if not text or not text.strip():
        return []

    lexicon = lexicon_from_names(known_entities)
    found: list[str] = []
    remaining = text
    # 1) Quoted spans first
    for m in _QUOTED.findall(text):
        if not is_stopword_entity(m):
            found.append(m.strip())

    # 2) Longest lexicon substring match (case-insensitive)
    lower = text.lower()
    occupied: list[tuple[int, int]] = []
    for name in lexicon:
        key = name.lower()
        start = 0
        while True:
            idx = lower.find(key, start)
            if idx < 0:
                break
            end = idx + len(key)
            # Word boundary-ish: avoid matching mid-token
            before_ok = idx == 0 or not text[idx - 1].isalnum()
            after_ok = end >= len(text) or not text[end].isalnum()
            if before_ok and after_ok and not _overlaps(idx, end, occupied):
                found.append(name)
                occupied.append((idx, end))
            start = idx + 1

    # 3) Title-case multi-word spans not already covered
    for m in _TITLE_SPAN.finditer(text):
        span = m.group(0).strip()
        if is_stopword_entity(span):
            continue
        # Drop leading stopword if multi-word: "The Apex Holdings" → keep if lexicon hit already
        words = span.split()
        if words and words[0].lower() in _STOPWORDS and len(words) > 1:
            span = " ".join(words[1:])
        if is_stopword_entity(span):
            continue
        if not _overlaps(m.start(), m.end(), occupied):
            # Prefer multi-word or non-stopword single tokens longer than 2 chars
            if " " in span or (len(span) > 2 and span.lower() not in _STOPWORDS):
                found.append(span)
                occupied.append((m.start(), m.end()))

    # 4) CJK spans
    for m in _CJK_SPAN.finditer(text):
        if not _overlaps(m.start(), m.end(), occupied):
            found.append(m.group(0))
            occupied.append((m.start(), m.end()))

    # Deduplicate preserving order; map to lexicon canonical form when possible
    canon_map = {normalize_entity_key(n): n for n in lexicon}
    out: list[str] = []
    seen: set[str] = set()
    for name in found:
        key = normalize_entity_key(name)
        if key in seen or is_stopword_entity(name):
            continue
        seen.add(key)
        out.append(canon_map.get(key, name))
        if len(out) >= max_entities:
            break

    # If we only got stopword-ish caps, drop them when lexicon had better options
    filtered = [n for n in out if not is_stopword_entity(n)]
    return filtered[:max_entities]


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for a, b in spans:
        if start < b and end > a:
            return True
    return False


def primary_entity(text: str, known_entities: list[str] | tuple[str, ...] | None = None) -> str | None:
    mentions = extract_entity_mentions(text, known_entities, max_entities=3)
    return mentions[0] if mentions else None
