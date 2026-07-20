"""Entity mention extraction from questions."""

from __future__ import annotations


def mentions_in_question(q: str) -> list[str]:
    """Pull capitalized multi-word mentions from the question for filtering."""
    from agentic_graphrag.agent.entities import extract_entity_mentions

    return extract_entity_mentions(q)
