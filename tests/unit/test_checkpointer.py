"""P2-AG-03: LangGraph checkpointer wiring + memory snapshot durability."""

from __future__ import annotations

import json

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agentic_graphrag.agent.checkpointer import make_checkpointer
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import build_graph, invoke_config, run_agentic_query
from agentic_graphrag.agent.loop_runtime import AgentRuntime, AgentState
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.config import resolve_path
from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.schema_check import Triple
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
    for line in (
        resolve_path("data/processed/seed_triples.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        if line.strip():
            triples.append(Triple.model_validate(json.loads(line)))
    entities, relations = triples_to_records(triples)
    return FakeGraphStore(entities, relations)


def _make_executor() -> Executor:
    store = _load_seed_store()
    graph = GraphRetriever(store)
    ft = BM25FulltextStore()
    ft.index(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d1",
                text=(
                    "Elena Varga is the CEO of Apex Holdings. "
                    "NovaTech Industries is a subsidiary of Apex Holdings."
                ),
                index=0,
            )
        ]
    )
    return Executor(
        graph=graph,
        vector=None,
        fulltext=FulltextRetriever(ft),
        llm=None,
        known_entities=[e.name for e in store.entities.values()],
    )


def test_make_checkpointer_memory():
    cp = make_checkpointer("memory")
    assert cp is not None
    assert type(cp).__name__ in ("MemorySaver", "InMemorySaver")


def test_make_checkpointer_unknown_raises():
    import pytest

    with pytest.raises(ValueError, match="Unknown checkpointer"):
        make_checkpointer("redis")


def test_build_graph_requires_thread_id_when_checkpointer_on():
    """Compiled graph with checkpointer rejects invoke without thread_id."""
    import pytest

    ex = _make_executor()
    g = build_graph(ex, None, GuardrailConfig(max_hops=2, recursion_limit=8))
    with pytest.raises(ValueError, match="thread_id"):
        g.invoke(
            {
                "question": "Who is CEO of Apex Holdings?",
                "chain": {"question": "x", "schema_version": "1.0.0", "query_id": "q"},
                "sub_questions": [],
                "current_index": 0,
                "hop": 0,
                "evidence": [],
                "done": False,
                "allow_llm": False,
            }
        )


def test_run_agentic_query_writes_checkpoint_with_memory_snapshot():
    ex = _make_executor()
    cp = MemorySaver()
    thread_id = "test-thread-checkpoint-1"
    chain = run_agentic_query(
        "Who is the CEO of Apex Holdings?",
        ex,
        None,
        guard_cfg=GuardrailConfig(max_hops=3, recursion_limit=12),
        allow_llm=False,
        checkpointer=cp,
        thread_id=thread_id,
    )
    assert chain.metadata.get("thread_id") == thread_id
    assert chain.metadata.get("checkpointer") is True

    # Re-compile against the same checkpointer to read final state
    g = build_graph(
        ex,
        None,
        GuardrailConfig(max_hops=3, recursion_limit=12),
        checkpointer=cp,
    )
    cfg = invoke_config(thread_id, recursion_limit=12)
    st = g.get_state(cfg)
    assert st.values.get("question")
    snap = st.values.get("memory_snapshot") or {}
    # Offline run should accumulate some evidence into shared memory
    assert "evidence" in snap or "explored_subquestions" in snap
    history = list(g.get_state_history(cfg))
    assert len(history) >= 2


def test_memory_hydrate_from_checkpoint_snapshot():
    """Fresh AgentRuntime restores MemoryState from state.memory_snapshot."""
    ex = _make_executor()
    mem = MemoryState()
    mem.exclude_hypothesis("bad path")
    mem.mark_subquestion_done("sq1", "Apex Holdings")
    mem.add_evidence(
        [
            Candidate(
                id="e1",
                source=CandidateSource.GRAPH_NEIGHBOR,
                content="Elena -[CEO_OF]-> Apex Holdings",
                score=1.0,
            )
        ]
    )
    state: AgentState = {
        "question": "q",
        "memory_snapshot": mem.to_snapshot(),
        "hop": 2,
        "chain": {},
    }
    # New runtime instance (simulates resume after rehydrate)
    rt2 = AgentRuntime(ex, None, GuardrailConfig(max_hops=2))
    rt2._hydrate_from_state(state)
    assert rt2.memory.is_excluded("bad path")
    assert rt2.memory.conclusions_by_subquestion["sq1"] == "Apex Holdings"
    assert "e1" in rt2.memory.evidence
    assert rt2.guards.state.hop == 2


def test_interrupt_resume_preserves_memory_via_checkpointer():
    """Interrupt after planner; resume continues with hydrated memory fields."""

    def planner(state: dict) -> dict:
        snap = dict(state.get("memory_snapshot") or {})
        snap["explored_subquestions"] = ["who is ceo"]
        return {**state, "memory_snapshot": snap, "phase": "planned"}

    def answer(state: dict) -> dict:
        snap = state.get("memory_snapshot") or {}
        assert "who is ceo" in (snap.get("explored_subquestions") or [])
        return {**state, "done": True, "answer": "ok"}

    g = StateGraph(dict)
    g.add_node("planner", planner)
    g.add_node("answer", answer)
    g.set_entry_point("planner")
    g.add_edge("planner", "answer")
    g.add_edge("answer", END)
    cp = MemorySaver()
    app = g.compile(checkpointer=cp, interrupt_after=["planner"])
    cfg = invoke_config("resume-thread")
    mid = app.invoke({"memory_snapshot": {}, "question": "q"}, config=cfg)
    assert mid.get("phase") == "planned"
    final = app.invoke(None, config=cfg)
    assert final.get("done") is True
    assert final.get("answer") == "ok"
    st = app.get_state(cfg)
    assert "who is ceo" in (st.values.get("memory_snapshot") or {}).get("explored_subquestions", [])
