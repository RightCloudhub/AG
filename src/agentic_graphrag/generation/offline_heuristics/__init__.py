"""Focused multi-hop extractive heuristics (offline answer path)."""

from __future__ import annotations

from agentic_graphrag.generation.offline_heuristics.extract import focused_extract
from agentic_graphrag.generation.offline_heuristics.mentions import mentions_in_question

__all__ = ["focused_extract", "mentions_in_question"]
