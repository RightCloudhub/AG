"""Text/edge parsers for M&A multi-hop heuristics."""

from __future__ import annotations

import re

from agentic_graphrag.generation.offline_heuristics.constants import DEMO_HQ
from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView

# Strict Title-Case company + city (no IGNORECASE on city — avoids "Austin and").
_HQ_DIRECT = re.compile(
    r"([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})\s+is\s+headquartered\s+in\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
)
_HQ_COMMA = re.compile(
    r"([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})\s+is\s+a\s+.+?,\s*"
    r"headquartered\s+in\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
)
_SUBSIDIARY_OF = re.compile(
    r"([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})\s+is\s+(?:an?\s+\w+\s+)?"
    r"(?:company\s+and\s+a\s+)?subsidiary\s+of\s+"
    r"([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})",
)
_PARENT_OF = re.compile(
    r"([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})\s+is\s+the\s+parent\s+company\s+of\s+"
    r"(.+?)(?:\.|$)",
)
_CEO_OF = re.compile(
    r"(?:current\s+)?CEO\s+of\s+([A-Z][A-Za-z0-9&.-]*(?:\s+[A-Z][A-Za-z0-9&.-]*){0,4})\s+is\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
)


def ownership_from_edges(view: EdgeView) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for h, t in view.find_edges("PARENT_OF"):
        out.setdefault(h.lower(), set()).add(t)
    for h, t in view.find_edges("SUBSIDIARY_OF"):
        out.setdefault(t.lower(), set()).add(h)
    return out


def ownership_from_texts(texts: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    blob = "\n".join(texts or [])
    for m in _SUBSIDIARY_OF.finditer(blob):
        child, parent = m.group(1).strip(), m.group(2).strip().rstrip(".,;")
        if looks_like_name(child) and looks_like_name(parent):
            out.setdefault(parent.lower(), set()).add(child)
    for m in _PARENT_OF.finditer(blob):
        parent = m.group(1).strip()
        if not looks_like_name(parent):
            continue
        tail = m.group(2).strip().rstrip(".,;")
        for part in re.split(r"\s+and\s+|,\s*", tail):
            child = re.split(r"\.\s+", part.strip())[0].strip()
            if looks_like_name(child):
                out.setdefault(parent.lower(), set()).add(child)
    return out


def ceos_from_edges(view: EdgeView) -> dict[str, str]:
    return {t.lower(): h for h, t in view.find_edges("CEO_OF")}


def ceos_from_texts(texts: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    blob = "\n".join(texts or [])
    for m in _CEO_OF.finditer(blob):
        out.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    return out


def hq_from_texts(texts: list[str]) -> dict[str, str]:
    # DEMO_HQ is evidence-independent seed for the demo corpus (see docs/IMPORTANT.md).
    out: dict[str, str] = dict(DEMO_HQ)
    blob = "\n".join(texts or [])
    for rx in (_HQ_DIRECT, _HQ_COMMA):
        for m in rx.finditer(blob):
            company, city = m.group(1).strip(), m.group(2).strip()
            if looks_like_name(company) and city and city.lower() not in {"and", "the", "a"}:
                out[company.lower()] = city
    return out


def looks_like_name(text: str) -> bool:
    if not text or len(text) > 60:
        return False
    if "\n" in text or text.lower().startswith(("the ", "a ", "an ")):
        return False
    return text[0].isupper()
