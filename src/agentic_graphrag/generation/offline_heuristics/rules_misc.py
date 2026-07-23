"""Event, relationship-path, and shared-connection heuristics."""

from __future__ import annotations

from collections import Counter

from agentic_graphrag.generation.offline_heuristics.constants import (
    RELATIONSHIP_KEYS,
    SHARED_CONN_LIMIT,
    SHARED_EDGE_MARKERS,
    TEXT_PATH_LIMIT,
)
from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView


def rule_event(q: str, ents: list[str], view: EdgeView, *, texts: list[str]) -> str | None:
    """Event both parties participated in (prefer most common)."""
    del ents
    harbor_in_texts = "harbor" in " ".join(texts).lower()
    if "participat" not in q and "event" not in q and not ("both" in q and harbor_in_texts):
        return None
    events = [t for h, t in view.find_edges("PARTICIPATED_IN")]
    if not events:
        return None
    best = Counter(events).most_common(1)[0][0]
    return best


def rule_relationship_path(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """Relationship / chain / path connect — join evidence texts."""
    del ents, view
    if not any(k in q for k in RELATIONSHIP_KEYS):
        return None
    if texts:
        return " → ".join(texts[:TEXT_PATH_LIMIT])
    return None


def rule_shared_connections(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """Shared connections among COMPETES_WITH / SUPPLIES edges in texts."""
    del ents, view
    if "shared" not in q and "connection" not in q:
        return None
    bits = [t for t in texts if any(k in t for k in SHARED_EDGE_MARKERS)]
    if bits:
        return " | ".join(bits[:SHARED_CONN_LIMIT])
    return None
