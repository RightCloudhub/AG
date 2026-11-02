"""Stable cross-encoder input without changing evidence or citation payloads."""

from __future__ import annotations

import re
from typing import Any

from agentic_graphrag.core.evidence import Candidate

_EDGE_ID = re.compile(r"(\[[^\]#]+)#[^\]]+(\])")


def edge_text(head: str, relation: str, tail: str) -> str:
    return f"{head} {relation.replace('_', ' ').lower()} {tail}."


def candidate_text(candidate: Candidate) -> str:
    """Render directed edges identically for neighbor and path candidates."""
    if not candidate.is_graph():
        return candidate.content
    data = candidate.structured
    if data.get("rerank_edges"):
        return " ".join(edge_text(**edge) for edge in data["rerank_edges"])
    if all(data.get(key) for key in ("head", "relation", "tail")):
        return edge_text(data["head"], data["relation"], data["tail"])
    # Legacy path candidates retain their arrows (including reverse traversal).
    return _EDGE_ID.sub(r"\1\2", candidate.content)


def score_key(query: str, candidate: Candidate, *, model: str) -> tuple[str, str, str]:
    # Graph paths may cite many chunks: never alias them to one source chunk.
    chunk_id = next((c.chunk_id for c in candidate.citations if c.chunk_id), None)
    identity = candidate.id if candidate.is_graph() else (chunk_id or candidate.id)
    return query, identity, model


def with_meta(candidate: Candidate, **metadata: Any) -> Candidate:
    return candidate.model_copy(update={"metadata": {**candidate.metadata, **metadata}})
