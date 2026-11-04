"""Monotonic physical identities for process-local index adapters (K-05).

Memory adapters keep one record per logical chunk and admit a write only when its
:class:`ChunkIdentity` is not lower than the stored record or the cleanup floor,
which is how a late write or cleanup from an obsolete worker is neutralised.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

from agentic_graphrag.core.ports.stores import ChunkIdentity, ChunkRecord


def admits(
    records: Mapping[str, ChunkRecord],
    floors: Mapping[str, ChunkIdentity],
    incoming: tuple[str, ChunkRecord],
    *,
    stage: str,
) -> bool:
    key, chunk = incoming
    identity = chunk.identity(stage)
    floor = floors.get(key)
    if floor is not None and identity < floor:
        return False
    stored = records.get(key)
    return stored is None or identity >= stored.identity(stage)


def remove_below(
    records: MutableMapping[str, ChunkRecord],
    floors: MutableMapping[str, ChunkIdentity],
    key: str,
    *,
    below: ChunkIdentity | None,
    stage: str,
) -> int:
    """Remove the logical chunk's record when it is older than ``below`` (or always)."""
    if below is not None:
        floors[key] = max(floors.get(key, below), below)
    stored = records.get(key)
    if stored is None or (below is not None and stored.identity(stage) >= below):
        return 0
    del records[key]
    return 1
