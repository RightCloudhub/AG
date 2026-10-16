"""Deterministic pattern extractor — the offline twin of the LLM extractor (BL-01).

Covers the sentence shapes of corporate-relations prose ("X is the CEO of Y",
"X produces A and B", "X was founded by P in 2012", "X的首席执行官是Y", …) so
the runtime upload path can build graph facts without an LLM. Precision over
recall: anything ambiguous is skipped and left to the LLM path.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple

RULE_CONFIDENCE = 0.85

_W = r"[A-Z][\w&.\-]*"
NAME = rf"(?:{_W}(?:\s+(?:{_W}|\d[\w\-]*|of|de|&))*)"
CJK = r"[\u4e00-\u9fff·]{2,12}"
ITEM = rf"(?:the\s+)?{NAME}"
LIST = rf"(?:both\s+)?{ITEM}(?:\s*,\s*{ITEM})*(?:,?\s+and\s+{ITEM})?"

_LEADING_NOISE = re.compile(r"^(?:[Tt]he|In|Through|During|Interestingly|[Bb]oth)\s+")
_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_YEAR_RANGE = re.compile(r"\bfrom\s+(\d{4})\s+to\s+(\d{4})\b")
_YEAR_SINCE = re.compile(r"\b(?:since|in|joining\s+\S+(?:\s+\S+)?\s+in)\s+(\d{4})\b|(\d{4})年")


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    relation: str
    head: tuple[str, str]  # (group, entity type)
    tail: tuple[str, str]
    dated: bool = False  # attach a year found in the sentence as valid_from


def _rule(rx: str, relation: str, types: tuple[str, str], *, dated: bool = False) -> Rule:
    """``types`` = (head entity type, tail entity type); groups are always ``h`` / ``t``."""
    return Rule(re.compile(rx), relation, ("h", types[0]), ("t", types[1]), dated=dated)


P, C, PR, EV, LOC = "Person", "Company", "Product", "Event", "Location"

RULES: tuple[Rule, ...] = (
    _rule(
        rf"(?P<h>{NAME}) is (?:also )?the (?:current )?CEO of (?P<t>{NAME})",
        "CEO_OF",
        (P, C),
        dated=True,
    ),
    _rule(
        rf"[Tt]he (?:current )?CEO of (?P<t>{NAME}) is (?P<h>{NAME})", "CEO_OF", (P, C), dated=True
    ),
    _rule(
        rf"(?P<h>{NAME}) (?:later )?became (?:the )?CEO of (?P<t>{NAME})",
        "CEO_OF",
        (P, C),
        dated=True,
    ),
    _rule(rf"(?P<h>{NAME}) is the parent company of (?P<t>{LIST})", "PARENT_OF", (C, C)),
    _rule(
        rf"(?P<h>{NAME}) is (?:a|an) [\w\s,-]*?subsidiary of (?P<t>{NAME})", "SUBSIDIARY_OF", (C, C)
    ),
    _rule(rf"(?P<h>{NAME}) (?:produces|makes) (?:the )?(?P<t>{LIST})", "PRODUCES", (C, PR)),
    _rule(rf"(?P<h>{NAME}) supplies [\w\s-]*? for (?P<t>{NAME})", "SUPPLIES_FOR", (C, PR)),
    _rule(
        rf"(?P<h>{NAME}) (?:also )?(?:provides|supplies) [\w\s-]*?(?:services )?to (?P<t>{LIST})",
        "SUPPLIES",
        (C, C),
    ),
    _rule(
        rf"(?P<h>{NAME}) (?:competes with|is (?:considered )?a competitor of) (?P<t>{NAME})",
        "COMPETES_WITH",
        (C, C),
    ),
    _rule(rf"(?P<h>{NAME}) acquired (?P<t>{NAME})", "ACQUIRED", (C, C), dated=True),
    _rule(
        rf"(?P<h>{NAME}) is [\w\s,-]*?(?:headquartered|based) in (?P<t>{NAME})",
        "LOCATED_IN",
        (C, LOC),
    ),
    _rule(
        rf"(?P<t>{NAME}) was founded (?:in \d{{4}} )?by (?P<h>{NAME})",
        "FOUNDED",
        (P, C),
        dated=True,
    ),
    _rule(rf"(?P<h>{NAME}) founded (?P<t>{NAME})", "FOUNDED", (P, C), dated=True),
    _rule(
        rf"(?P<h>{NAME}) (?:previously |also |later )?worked at (?P<t>{LIST})", "WORKED_AT", (P, C)
    ),
    _rule(rf"(?P<h>{NAME}) (?:also )?participated in (?P<t>{NAME})", "PARTICIPATED_IN", (C, EV)),
    _rule(rf"(?P<h>{NAME}) led the (?P<t>{NAME})", "PARTICIPATED_IN", (C, EV)),
    _rule(
        rf"(?P<h>{NAME}) is (?:a|the) (?:strategic )?partner of (?P<t>{NAME})", "PARTNER_OF", (C, C)
    ),
    _rule(
        rf"(?P<t>{CJK}|{NAME})的(?:首席执行官|CEO)是(?P<h>{CJK}|{NAME})",
        "CEO_OF",
        (P, C),
        dated=True,
    ),
    _rule(
        rf"(?P<h>{CJK}|{NAME})是(?P<t>{CJK}|{NAME})的(?:首席执行官|CEO)",
        "CEO_OF",
        (P, C),
        dated=True,
    ),
    _rule(rf"(?P<h>{CJK}|{NAME})是(?P<t>{CJK}|{NAME})的母公司", "PARENT_OF", (C, C)),
    _rule(
        rf"(?P<h>{CJK}|{NAME})(?:收购了|收购)(?P<t>{CJK}|{NAME})", "ACQUIRED", (C, C), dated=True
    ),
)


def split_list(text: str) -> list[str]:
    parts = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", text.strip())
    names = [_clean(p) for p in parts]
    return [n for n in names if n]


def _clean(name: str) -> str:
    out = _LEADING_NOISE.sub("", name.strip().rstrip(".,;"))
    return out.strip()


def sentence_dates(sentence: str) -> dict[str, str]:
    """``valid_from`` / ``valid_to`` stated in a sentence ("from 2010 to 2016", "in 2019")."""
    span = _YEAR_RANGE.search(sentence)
    if span:
        return {"valid_from": span.group(1), "valid_to": span.group(2)}
    single = _YEAR_SINCE.search(sentence)
    if single:
        return {"valid_from": single.group(1) or single.group(2)}
    return {}


def _matches(sentence: str) -> Iterator[tuple[Rule, re.Match[str]]]:
    for rule in RULES:
        for match in rule.pattern.finditer(sentence):
            yield rule, match


def extract_sentence(sentence: str) -> list[Triple]:
    out: list[Triple] = []
    for rule, match in _matches(sentence):
        heads = split_list(match.group(rule.head[0]))
        tails = split_list(match.group(rule.tail[0]))
        dates = _dates_for(rule, sentence)
        for head in heads:
            out.extend(_pairs(rule, (head, tails), (sentence, dates)))
    return out


def _dates_for(rule: Rule, sentence: str) -> dict[str, str]:
    if rule.dated:
        return sentence_dates(sentence)
    span = _YEAR_RANGE.search(sentence)  # employment spans: "from 2010 to 2016"
    return {"valid_from": span.group(1), "valid_to": span.group(2)} if span else {}


def _pairs(
    rule: Rule, ends: tuple[str, list[str]], ctx: tuple[str, dict[str, str]]
) -> list[Triple]:
    head, tails = ends
    sentence, dates = ctx
    return [
        Triple(
            head=EntityMention(name=head, type=rule.head[1]),
            relation=rule.relation,
            tail=EntityMention(name=tail, type=rule.tail[1]),
            confidence=RULE_CONFIDENCE,
            source_span=sentence.strip()[:300],
            attributes=dict(dates),
        )
        for tail in tails
        if tail and tail.lower() != head.lower()
    ]


def extract_rules_from_chunk(chunk: ChunkRecord) -> list[Triple]:
    """All rule triples of a chunk, provenance-stamped and de-duplicated."""
    seen: set[tuple[str, str, str]] = set()
    out: list[Triple] = []
    for sentence in _SENTENCE.split(chunk.text):
        for triple in extract_sentence(sentence):
            key = (triple.head.name.lower(), triple.relation, triple.tail.name.lower())
            if key in seen:
                continue
            seen.add(key)
            triple.source_doc_id = chunk.doc_id
            triple.source_chunk_id = chunk.chunk_id
            out.append(triple)
    return out
