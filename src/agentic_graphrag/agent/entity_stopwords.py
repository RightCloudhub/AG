"""Stopword sets and small entity name predicates."""

from __future__ import annotations

import re

STOPWORDS = frozenset(
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
        "ceo",
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
        "acquired",
        "acquire",
        "acquisition",
        "headquartered",
        "headquarters",
        "city",
    }
)

# Chinese interrogative / function spans that must not become entity mentions.
CJK_STOPWORDS = frozenset(
    {
        "的",
        "了",
        "吗",
        "呢",
        "哪",
        "哪个",
        "哪些",
        "哪里",
        "哪儿",
        "什么",
        "谁",
        "何时",
        "为何",
        "为什么",
        "怎么",
        "如何",
        "多少",
        "是否",
        "以及",
        "或者",
        "还是",
        "后来",
        "然后",
        "所在",
        "所在公司",
        "公司",
        "收购",
        "收购了",
        "被收购",
        "被收购公司",
        "哪家",
        "哪家公司",
        "后来收购了哪家公司",
        "那家",
        "那家被收购公司",
        "那家被收购公司的总部在哪个城市",
        "总部",
        "总部在",
        "总部位于",
        "哪个城市",
        "在哪个城市",
        "城市",
        "母公司",
        "子公司",
        "首席执行官",
        "总裁",
    }
)


def is_stopword_entity(name: str) -> bool:
    key = name.strip().lower()
    if not key or key in STOPWORDS:
        return True
    raw = name.strip()
    if raw in CJK_STOPWORDS:
        return True
    # Pure CJK spans that are entirely function words / question templates.
    if _is_cjk_only(raw) and (raw in CJK_STOPWORDS or _cjk_is_function_span(raw)):
        return True
    if " " not in key and key in STOPWORDS:
        return True
    return False


def _is_cjk_only(text: str) -> bool:
    return bool(text) and all("\u4e00" <= ch <= "\u9fff" for ch in text)


# Interrogative / discourse fragments only — never bare surname-capable chars (e.g. 何).
_CJK_FUNCTION_PARTICLES = (
    "哪",
    "什么",
    "谁",
    "如何",
    "为何",
    "何时",
    "怎么",
    "怎样",
    "怎",
    "吗",
    "呢",
    "哪家",
    "后来",
    "那家",
)
_CJK_FUNCTION_SPAN_MAX_LEN = 20


def _cjk_is_function_span(text: str) -> bool:
    """Heuristic: pure-CJK spans that look like question templates / function phrases.

    Bare ``何`` is intentionally omitted: it is a common surname (何强, 何氏集团).
    Multi-char interrogatives (如何 / 为何 / 何时) cover the same question patterns.
    """
    if len(text) <= 1:
        return True
    if len(text) > _CJK_FUNCTION_SPAN_MAX_LEN:
        return False
    return any(p in text for p in _CJK_FUNCTION_PARTICLES)


def normalize_entity_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())
