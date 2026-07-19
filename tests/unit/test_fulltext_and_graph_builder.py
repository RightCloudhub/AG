import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agentic_graphrag.cli import _open_graph_store, build_graph_main
from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import ChunkRecord


def _write_seed_triple(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "head": {"name": "Elena Varga", "type": "Person"},
                "relation": "CEO_OF",
                "tail": {"name": "Apex Holdings", "type": "Company"},
                "confidence": 0.95,
                "source_doc_id": "d1",
                "source_chunk_id": "c1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class _Settings:
    neo4j_uri = "bolt://localhost:7687"
    neo4j_user = "neo4j"
    neo4j_password = "x"


class _BrokenNeo4j:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def ping(self) -> None:
        raise ConnectionError("connection refused (test)")

    def close(self) -> None:
        return None


def test_bm25_search():
    store = BM25FulltextStore()
    store.index(
        [
            ChunkRecord(chunk_id="1", doc_id="d", text="Elena Varga is CEO of Apex Holdings", index=0),
            ChunkRecord(chunk_id="2", doc_id="d", text="QuantumEdge Server is a product", index=1),
        ]
    )
    hits = store.search("Elena CEO Apex", top_k=5)
    assert hits
    assert hits[0][0].chunk_id == "1"


def test_triples_to_records_dedupe_entities():
    triples = [
        Triple(
            head=EntityMention(name="Elena Varga", type="Person"),
            relation="CEO_OF",
            tail=EntityMention(name="Apex Holdings", type="Company"),
            confidence=0.9,
        ),
        Triple(
            head=EntityMention(name="Elena Varga", type="Person"),
            relation="WORKED_AT",
            tail=EntityMention(name="Orion Systems", type="Company"),
            confidence=0.8,
        ),
    ]
    entities, relations = triples_to_records(triples)
    assert len(entities) == 3
    assert len(relations) == 2


def test_build_graph_main_memory_graph_offline(tmp_path: Path, capsys):
    """--memory-graph always uses process-local store (no Neo4j)."""
    triples_path = _write_seed_triple(tmp_path / "seed.jsonl")
    build_graph_main(["--triples", str(triples_path), "--no-llm", "--memory-graph"])
    out = capsys.readouterr().out
    assert "Schema-valid triples: 1" in out
    assert '"backend": "memory"' in out
    assert '"nodes": 2' in out


def test_build_graph_main_no_llm_falls_back_when_neo4j_down(tmp_path: Path, capsys):
    """Documented offline command: --triples + --no-llm must not crash without Neo4j."""
    triples_path = _write_seed_triple(tmp_path / "seed.jsonl")
    with patch("agentic_graphrag.stores.neo4j_store.Neo4jGraphStore", _BrokenNeo4j):
        build_graph_main(["--triples", str(triples_path), "--no-llm"])
    captured = capsys.readouterr()
    assert "Schema-valid triples: 1" in captured.out
    assert '"backend": "memory"' in captured.out
    assert "falling back to in-memory" in captured.err


def test_open_graph_store_memory_flag():
    store, backend = _open_graph_store(_Settings(), memory=True)
    assert backend == "memory"
    store.close()


def test_open_graph_store_fallback_and_hard_fail():
    with patch("agentic_graphrag.stores.neo4j_store.Neo4jGraphStore", _BrokenNeo4j):
        store, backend = _open_graph_store(_Settings(), allow_memory_fallback=True)
        assert backend == "memory"
        store.close()

        with pytest.raises(SystemExit) as ei:
            _open_graph_store(_Settings(), allow_memory_fallback=False)
        assert ei.value.code == 1
