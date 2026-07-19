"""Edge parsing helpers for offline extractive answer heuristics."""

from __future__ import annotations

import re

_EDGE = re.compile(
    r"(.+?)\s+-\[([A-Z_]+)\]->\s+(.+?)(?:\s+\([^)]*\))?\s*$",
    re.I,
)


def parse_edges(texts: list[str]) -> list[tuple[str, str, str]]:
    edges: list[tuple[str, str, str]] = []
    for t in texts:
        m = _EDGE.search(t.strip())
        if not m:
            continue
        head = m.group(1).strip()
        rel = m.group(2).strip().upper()
        tail = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(3)).strip()
        edges.append((head, rel, tail))
    return edges
