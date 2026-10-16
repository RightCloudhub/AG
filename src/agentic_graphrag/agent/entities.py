"""Entity mention extraction for offline / heuristic tool selection.

Prefers longest matches against a known entity lexicon (from the graph/corpus)
and filters interrogative stopwords so graph tools never target "Who"/"Which".
"""

from __future__ import annotations

from agentic_graphrag.agent.entity_mentions import (
    default_lexicon_from_seed,
    extract_entity_mentions,
    lexicon_from_names,
    primary_entity,
)
from agentic_graphrag.agent.entity_stopwords import (
    is_stopword_entity,
    normalize_entity_key,
)

__all__ = [
    "default_lexicon_from_seed",
    "extract_entity_mentions",
    "is_stopword_entity",
    "lexicon_from_names",
    "normalize_entity_key",
    "primary_entity",
]
