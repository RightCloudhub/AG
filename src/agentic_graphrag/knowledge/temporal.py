"""Temporal validity of graph facts (docs/design/BUSINESS_LOGIC.md BL-14).

A relation may carry ``valid_from`` / ``valid_to`` in its ``attributes``
(ISO ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``). An open ``valid_to`` means the
fact is still current. Conflicts between dated facts are decided by time, not
by extraction confidence, and a superseded fact is *closed* rather than deleted
so history survives.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

VALID_FROM = "valid_from"
VALID_TO = "valid_to"

_DATE = re.compile(r"^\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?\s*$")

TimeKey = tuple[int, int, int]


def time_key(value: Any) -> TimeKey | None:
    """Parse an ISO-ish date (year, year-month, or full date) into a sortable key."""
    if value is None:
        return None
    match = _DATE.match(str(value))
    if match is None:
        return None
    year, month, day = match.groups()
    return (int(year), int(month or 1), int(day or 1))


def normalize_date(value: Any) -> str | None:
    """Canonical string for a parsable date, else ``None``."""
    key = time_key(value)
    if key is None:
        return None
    raw = str(value).strip()
    return raw if raw.count("-") else f"{key[0]:04d}"


def valid_from(attrs: dict[str, Any] | None) -> TimeKey | None:
    return time_key((attrs or {}).get(VALID_FROM))


def valid_to(attrs: dict[str, Any] | None) -> TimeKey | None:
    return time_key((attrs or {}).get(VALID_TO))


def is_dated(attrs: dict[str, Any] | None) -> bool:
    return valid_from(attrs) is not None or valid_to(attrs) is not None


def is_current(attrs: dict[str, Any] | None, as_of: date | None = None) -> bool:
    """True when the fact has not ended by ``as_of`` (today by default)."""
    end = valid_to(attrs)
    if end is None:
        return True
    today = as_of or date.today()
    return end > (today.year, today.month, today.day)


def is_newer(candidate: dict[str, Any] | None, incumbent: dict[str, Any] | None) -> bool | None:
    """Compare start dates: True/False when both are dated, ``None`` when undecidable."""
    new_start = valid_from(candidate)
    old_start = valid_from(incumbent)
    if new_start is None or old_start is None or new_start == old_start:
        return None
    return new_start > old_start


def closed(attrs: dict[str, Any] | None, end: Any) -> dict[str, Any]:
    """Copy of ``attrs`` with ``valid_to`` set (an existing earlier end wins)."""
    out = dict(attrs or {})
    current_end = valid_to(out)
    new_end = time_key(end)
    if new_end is not None and (current_end is None or new_end < current_end):
        out[VALID_TO] = normalize_date(end)
    return out


def prefer_incoming(
    incoming: tuple[dict[str, Any] | None, float],
    existing: tuple[dict[str, Any] | None, float],
) -> bool:
    """Same-edge merge rule: the later-dated fact wins; otherwise higher confidence."""
    newer = is_newer(incoming[0], existing[0])
    if newer is not None:
        return newer
    return incoming[1] > existing[1]


_PAST_CUES = re.compile(
    r"\b(previous(ly)?|former(ly)?|before|used to|histor(y|ical)|past|ex-|in \d{4})\b"
    r"|曾|以前|此前|前任|历史|过去",
    re.IGNORECASE,
)
HISTORICAL_EDGE_WEIGHT = 0.5


def asks_about_past(text: str | None) -> bool:
    return bool(text and _PAST_CUES.search(text))


def temporal_weight(attrs: dict[str, Any] | None, question: str | None) -> float:
    """Down-rank closed (historical) facts unless the question asks about the past."""
    if is_current(attrs) or asks_about_past(question):
        return 1.0
    return HISTORICAL_EDGE_WEIGHT


def validity_label(attrs: dict[str, Any] | None) -> str:
    """`` [2019–now]`` style suffix for dated facts; empty when undated."""
    if not is_dated(attrs):
        return ""
    start = (attrs or {}).get(VALID_FROM) or "?"
    end = (attrs or {}).get(VALID_TO) or "now"
    return f" [{start}–{end}]"
