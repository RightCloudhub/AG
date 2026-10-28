"""Graph-degree prior for ambiguous short entity mentions.

``extract_entity_mentions`` refuses to expand an ambiguous prefix ("NovaTech"
→ NovaTech Industries vs NovaTech Edge Labs Spin-in) and passes the bare
surface form on, which matches no graph node, so retrieval returns nothing.
A standard entity-linking prior resolves it: prefer the candidate that is far
better connected in the graph, and only when it clearly dominates.
"""

from __future__ import annotations

from agentic_graphrag.core.ports.stores import GraphStore

# The best candidate must have at least this many times the runner-up's degree.
_DOMINANCE = 2
_DEGREE_PROBE_LIMIT = 100


def link_ambiguous_mention(name: str, known: list[str], store: GraphStore) -> str:
    """Expand ``name`` to the dominant known entity it prefixes, else keep it."""
    key = name.strip().lower()
    if not key or any(k.lower() == key for k in known):
        return name
    candidates = [k for k in known if k.lower().startswith(key + " ")]
    if len(candidates) < 2:
        return name
    ranked = sorted(((_degree(store, c), c) for c in candidates), key=lambda t: (-t[0], t[1]))
    best, runner_up = ranked[0], ranked[1]
    if best[0] > 0 and best[0] >= _DOMINANCE * max(1, runner_up[0]):
        return best[1]
    return name


def _degree(store: GraphStore, name: str) -> int:
    try:
        return len(store.neighbors(name, max_hops=1, limit=_DEGREE_PROBE_LIMIT))
    except Exception:  # noqa: BLE001 — a missing node simply has no prior
        return 0
