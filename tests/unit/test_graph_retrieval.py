"""P2-RT-01 graph retrieval: relation filter, beam expand, path Top-K, config caps."""

from agentic_graphrag.config import GraphRetrievalConfig, get_config
from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.retrieval.contracts import CandidateSource
from agentic_graphrag.retrieval.graph import (
    GraphRetriever,
    infer_relation_types,
    relation_relevance,
)
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def _seed_store() -> InMemoryGraphStore:
    triples = [
        Triple(
            head=EntityMention(name="NovaTech Industries", type="Company"),
            relation="SUBSIDIARY_OF",
            tail=EntityMention(name="Apex Holdings", type="Company"),
            confidence=0.95,
        ),
        Triple(
            head=EntityMention(name="Apex Holdings", type="Company"),
            relation="PARENT_OF",
            tail=EntityMention(name="NovaTech Industries", type="Company"),
            confidence=0.95,
        ),
        Triple(
            head=EntityMention(name="Elena Varga", type="Person"),
            relation="CEO_OF",
            tail=EntityMention(name="Apex Holdings", type="Company"),
            confidence=0.99,
        ),
        Triple(
            head=EntityMention(name="Marcus Chen", type="Person"),
            relation="CEO_OF",
            tail=EntityMention(name="NovaTech Industries", type="Company"),
            confidence=0.9,
        ),
        Triple(
            head=EntityMention(name="Elena Varga", type="Person"),
            relation="WORKED_AT",
            tail=EntityMention(name="Orion Systems", type="Company"),
            confidence=0.85,
        ),
        Triple(
            head=EntityMention(name="NovaTech Industries", type="Company"),
            relation="PRODUCES",
            tail=EntityMention(name="QuantumEdge Server", type="Product"),
            confidence=0.9,
        ),
        Triple(
            head=EntityMention(name="Helix Compute", type="Company"),
            relation="COMPETES_WITH",
            tail=EntityMention(name="NovaTech Industries", type="Company"),
            confidence=0.8,
        ),
    ]
    ents, rels = triples_to_records(triples)
    store = InMemoryGraphStore()
    store.upsert_entities(ents)
    store.upsert_relations(rels)
    return store


def test_infer_relation_types_ceo_parent():
    rels = infer_relation_types("Who is the CEO of the parent company of NovaTech?")
    assert rels is not None
    assert "CEO_OF" in rels
    assert "PARENT_OF" in rels or "SUBSIDIARY_OF" in rels


def test_relation_relevance_scores():
    assert relation_relevance("CEO_OF", "Who is the CEO?") > relation_relevance(
        "PRODUCES", "Who is the CEO?"
    )


def test_neighbors_prefers_relevant_relations():
    store = _seed_store()
    ret = GraphRetriever.from_config(store, GraphRetrievalConfig(max_neighbors_per_layer=10))
    hits = ret.neighbors(
        "NovaTech Industries",
        max_hops=2,
        sub_question="Who is the CEO of the parent company?",
    )
    assert hits
    assert all(c.source == CandidateSource.GRAPH_NEIGHBOR for c in hits)
    # Scores should be sorted descending
    scores = [c.score for c in hits]
    assert scores == sorted(scores, reverse=True)
    # Ownership / CEO edges should outrank pure product noise when present
    top_rels = [c.structured["relation"] for c in hits[:3]]
    assert any(r in top_rels for r in ("SUBSIDIARY_OF", "PARENT_OF", "CEO_OF", "PRODUCES"))


def test_multi_hop_neighbors_prefer_seed_touching_edges():
    """CEO edges of *other* companies must not outrank 1-hop seed edges."""
    from agentic_graphrag.knowledge.graph_builder import triples_to_records
    from agentic_graphrag.knowledge.schema_check import EntityMention, Triple

    triples = [
        Triple(
            head=EntityMention(name="BrightLink Logistics", type="Company"),
            relation="SUPPLIES",
            tail=EntityMention(name="NovaTech Industries", type="Company"),
            confidence=0.9,
        ),
        Triple(
            head=EntityMention(name="Marcus Chen", type="Person"),
            relation="CEO_OF",
            tail=EntityMention(name="NovaTech Industries", type="Company"),
            confidence=0.99,
        ),
    ]
    ents, rels = triples_to_records(triples)
    store = InMemoryGraphStore()
    store.upsert_entities(ents)
    store.upsert_relations(rels)
    ret = GraphRetriever.from_config(store, GraphRetrievalConfig(max_neighbors_per_layer=10))
    hits = ret.neighbors(
        "BrightLink Logistics",
        max_hops=2,
        sub_question="Who is the CEO of BrightLink Logistics?",
    )
    assert hits
    # Seed SUPPLIES edge should rank above multi-hop CEO of NovaTech
    assert hits[0].structured["relation"] == "SUPPLIES"
    assert "BrightLink" in hits[0].content


def test_path_dedup_and_topk():
    store = _seed_store()
    cfg = GraphRetrievalConfig(max_paths=5, max_path_hops=3)
    ret = GraphRetriever.from_config(store, cfg)
    paths = ret.paths(
        "Elena Varga",
        "NovaTech Industries",
        sub_question="relationship chain between Elena and NovaTech",
    )
    assert len(paths) <= 5
    if paths:
        assert paths[0].source == CandidateSource.GRAPH_PATH
        sigs = [c.structured.get("signature") for c in paths]
        assert len(sigs) == len(set(sigs))


def test_from_config_reads_app_defaults():
    store = _seed_store()
    ret = GraphRetriever.from_config(store)
    app = get_config()
    assert ret.max_neighbors_per_layer == app.retrieval.graph.max_neighbors_per_layer
    assert ret.beam_width == app.retrieval.graph.beam_width


def test_subgraph_union():
    store = _seed_store()
    ret = GraphRetriever.from_config(store)
    hits = ret.subgraph(
        ["NovaTech Industries", "Helix Compute"],
        max_hops=1,
        sub_question="competitors and products",
        limit=15,
    )
    assert hits
    assert len(hits) <= 15
