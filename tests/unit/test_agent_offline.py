"""Offline agent loop using seed graph data without Neo4j/LLM network."""

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource
from agentic_graphrag.retrieval.fulltext import FulltextRetriever
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.fulltext_store import BM25FulltextStore
from agentic_graphrag.stores.interfaces import (
    ChunkRecord,
    EntityRecord,
    PathRecord,
    RelationRecord,
)
from agentic_graphrag.config import resolve_path
import json


class FakeGraphStore:
    def __init__(self, entities: list[EntityRecord], relations: list[RelationRecord]) -> None:
        self.entities = {e.name.lower(): e for e in entities}
        self.relations = relations
        self._by_id = {e.id: e for e in entities}

    def clear(self) -> None:
        return None

    def upsert_entities(self, entities: list[EntityRecord]) -> int:
        return len(entities)

    def upsert_relations(self, relations: list[RelationRecord]) -> int:
        return len(relations)

    def get_entity_by_name(self, name: str, entity_type: str | None = None):
        return self.entities.get(name.lower())

    def neighbors(self, entity_name, *, max_hops=1, relation_types=None, limit=50):
        out = []
        for r in self.relations:
            if r.head_name.lower() == entity_name.lower():
                if relation_types and r.type not in relation_types:
                    continue
                tail = self._by_id.get(r.tail_id) or EntityRecord(
                    id=r.tail_id, name=r.tail_name, type="Entity"
                )
                out.append((r, tail))
            elif r.tail_name.lower() == entity_name.lower():
                if relation_types and r.type not in relation_types:
                    continue
                head = self._by_id.get(r.head_id) or EntityRecord(
                    id=r.head_id, name=r.head_name, type="Entity"
                )
                out.append((r, head))
        return out[:limit]

    def paths(self, source_name, target_name, *, max_hops=4, limit=20):
        # 1-hop only for fake store
        for r in self.relations:
            if (
                r.head_name.lower() == source_name.lower()
                and r.tail_name.lower() == target_name.lower()
            ) or (
                r.tail_name.lower() == source_name.lower()
                and r.head_name.lower() == target_name.lower()
            ):
                h = EntityRecord(id=r.head_id, name=r.head_name, type="Entity")
                t = EntityRecord(id=r.tail_id, name=r.tail_name, type="Entity")
                return [PathRecord(nodes=[h, t], relations=[r], length=1, score=1.0)]
        return []

    def counts(self):
        return {"nodes": len(self.entities), "relationships": len(self.relations)}

    def close(self):
        return None


def _load_seed_store() -> FakeGraphStore:
    triples = []
    for line in resolve_path("data/processed/seed_triples.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            triples.append(Triple.model_validate(json.loads(line)))
    entities, relations = triples_to_records(triples)
    return FakeGraphStore(entities, relations)


def test_offline_agent_returns_chain():
    store = _load_seed_store()
    graph = GraphRetriever(store)
    ft = BM25FulltextStore()
    ft.index(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d1",
                text="Elena Varga is the CEO of Apex Holdings. NovaTech Industries is a subsidiary of Apex Holdings.",
                index=0,
            )
        ]
    )
    executor = Executor(
        graph=graph,
        vector=None,
        fulltext=FulltextRetriever(ft),
        llm=None,
    )
    chain = run_agentic_query(
        "Who is the CEO of Apex Holdings?",
        executor,
        None,
        guard_cfg=GuardrailConfig(max_hops=3, max_llm_calls=10, max_tokens=10000),
        allow_llm=False,
        recursion_limit=12,
    )
    assert chain.question
    assert chain.answer
    assert chain.status.value in {"answered", "partial", "no_answer"}
    assert isinstance(chain.steps, list)


def test_memory_prevents_duplicate_paths():
    m = MemoryState()
    c = Candidate(id="1", source=CandidateSource.GRAPH, content="A -[X]-> B")
    m.add_evidence([c])
    assert m.is_path_explored("a -[x]-> b")
