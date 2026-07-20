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
    }
)


def is_stopword_entity(name: str) -> bool:
    key = name.strip().lower()
    if not key or key in STOPWORDS:
        return True
    if " " not in key and key in STOPWORDS:
        return True
    return False


def normalize_entity_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())
