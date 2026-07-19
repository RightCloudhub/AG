"""Unit tests for entity mention extraction (real shipped helpers)."""

from agentic_graphrag.agent.entities import (
    extract_entity_mentions,
    is_stopword_entity,
    primary_entity,
)


LEXICON = [
    "NovaTech Industries",
    "Apex Holdings",
    "Elena Varga",
    "Marcus Chen",
    "Helix Compute",
    "QuantumEdge Server",
    "BrightLink Logistics",
    "Meridian Capital",
    "Orion Systems",
    "SiliconForge",
    "Harbor Components",
    "Priya Nair",
]


def test_stopwords_who_which():
    assert is_stopword_entity("Who")
    assert is_stopword_entity("Which")
    assert is_stopword_entity("CEO")
    assert not is_stopword_entity("NovaTech Industries")


def test_primary_entity_not_who():
    q = "Who is the CEO of the parent company of NovaTech Industries?"
    mentions = extract_entity_mentions(q, LEXICON)
    assert mentions
    assert mentions[0] == "NovaTech Industries"
    assert "Who" not in mentions
    assert "Which" not in mentions
    assert primary_entity(q, LEXICON) == "NovaTech Industries"


def test_multiword_and_person():
    q = "Which companies did the CEO of Apex Holdings previously work at?"
    mentions = extract_entity_mentions(q, LEXICON)
    assert "Apex Holdings" in mentions
    assert all(m.lower() not in {"who", "which", "what", "ceo"} for m in mentions)


def test_two_entities_path_question():
    q = "What is the relationship chain between Elena Varga and QuantumEdge Server?"
    mentions = extract_entity_mentions(q, LEXICON)
    assert "Elena Varga" in mentions
    assert "QuantumEdge Server" in mentions
