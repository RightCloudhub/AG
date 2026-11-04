"""Open graph ingestion is strict, scoped, and independent of demo extraction rules."""

import hashlib
import json

import pytest

from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.stores.entity_index import SQLiteEntityIndex
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore
from agentic_graphrag.stores.scope_codec import ScopeBinding
from agentic_graphrag.stores.scoped_graph import ScopedGraphStore


class OpenLLM:
    def __init__(self, payload, *, action="separate"):
        self.payload, self.action = payload, action
        self.calls = []
        self.embeddings = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        if "Decide whether" in messages[0].content:
            candidates = json.loads(messages[1].content)["candidates"]
            return json.dumps(
                {
                    "action": self.action,
                    "candidate_id": candidates[0]["entity_id"] if self.action == "merge" else None,
                    "reason": "Context identifies or distinguishes the person",
                }
            )
        return json.dumps(self.payload, ensure_ascii=False)

    def embed_many(self, texts):
        self.embeddings.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    def embed(self, text):
        return [1.0, 0.0]


def payload(*, name="王伟", description="合同审核人"):
    return {
        "entities": [
            {
                "ref": "person",
                "name": name,
                "entity_type": "人",
                "description": description,
                "evidence": {"quote": name},
            },
            {
                "ref": "contract",
                "name": "合同",
                "entity_type": "文档",
                "description": "受审合同",
                "evidence": {"quote": "合同"},
            },
        ],
        "relations": [
            {
                "head": "person",
                "tail": "contract",
                "predicate": "负责审核（合同）",
                "description": "该人员负责合同审核",
                "evidence": {"quote": f"{name}负责合同。"},
            }
        ],
    }


def chunk(kb, *, doc_id="d1", text="王伟负责合同。"):
    return ChunkRecord(
        chunk_id=f"c-{doc_id}",
        doc_id=doc_id,
        text=text,
        index=0,
        tenant_id=kb.tenant_id,
        kb_id=kb.kb_id,
        metadata={
            "document_version": 1,
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        },
    )


def setup_open(tmp_path, *, data=None, judge=True):
    kb = KnowledgeBase(tenant_id="t", kb_id="contracts", build_mode="open")
    graph = ScopedGraphStore(InMemoryGraphStore(), ScopeBinding(kb.scope))
    index = SQLiteEntityIndex(tmp_path / "entities.sqlite3")
    llm = OpenLLM(data or payload())
    queue = ReviewQueue(tmp_path / "review.jsonl")
    ingest = GraphIngestor(
        GraphIngestDeps(
            graph=graph,
            knowledge_base=kb,
            llm=llm,
            entity_index=index,
            embedding_space="provider:model:revision:dim2",
            embedder=llm,
            resolution_llm=llm if judge else None,
            review_queue=queue,
        )
    )
    return kb, graph, index, llm, queue, ingest


def test_free_chinese_relations_and_independent_entities(tmp_path, monkeypatch):
    data = payload()
    data["entities"].append(
        {
            "ref": "extra",
            "name": "附件",
            "entity_type": "独立文档",
            "description": "没有关系的附件",
            "evidence": {"quote": "附件"},
        }
    )
    kb, graph, index, llm, _queue, ingest = setup_open(tmp_path, data=data)

    def forbidden(*args, **kwargs):
        pytest.fail("Open mode called a Schema/default rule extractor")

    monkeypatch.setattr(
        "agentic_graphrag.knowledge.graph_ingest.extract_rules_from_chunk", forbidden
    )
    monkeypatch.setattr("agentic_graphrag.knowledge.graph_ingest.default_schema", forbidden)
    result = ingest.ingest_chunks([chunk(kb, text="王伟负责合同。附件")], tenant_id="t")
    assert result.accepted == 1 and result.entities_accepted == 3
    assert result.extractor == "open_llm"
    assert graph.counts()["nodes"] == 3
    relation = graph.list_relations()[0]
    assert relation.type == "负责审核（合同）"
    assert relation.sources[0]["scope"]["kb_id"] == "contracts"
    assert relation.sources[0]["doc_version"] == 1
    assert relation.sources[0]["span_origin"] == "chunk"
    assert len(llm.calls) == 1
    index.close()


def test_same_name_high_embedding_similarity_does_not_merge_people(tmp_path):
    kb, graph, index, llm, _queue, ingest = setup_open(tmp_path)
    ingest.ingest_chunks([chunk(kb)], tenant_id="t")
    first = next(e.id for e in graph.list_entities() if e.name == "王伟")
    llm.payload = payload(description="另一家公司同名审查人")
    ingest.ingest_chunks([chunk(kb, doc_id="d2")], tenant_id="t")
    people = [e for e in graph.list_entities() if e.name == "王伟"]
    assert len(people) == 2 and first in {e.id for e in people}
    assert graph.get_entity_by_name("王伟", tenant_id="t") is None
    index.close()


def test_contextual_merge_and_confirmed_alias_keep_canonical_id(tmp_path):
    kb, graph, index, llm, _queue, ingest = setup_open(tmp_path)
    ingest.ingest_chunks([chunk(kb)], tenant_id="t")
    original = next(e for e in graph.list_entities() if e.name == "王伟")
    index.confirm_alias("老王", original.id, scope=kb.scope, space="provider:model:revision:dim2")
    llm.payload = payload(name="老王")
    ingest.ingest_chunks([chunk(kb, doc_id="d2", text="老王负责合同。")], tenant_id="t")
    aliases = next(e for e in graph.list_entities() if e.id == original.id)
    assert "老王" in aliases.aliases and len(aliases.sources) == 2
    assert len([e for e in graph.list_entities() if e.type == "人"]) == 1
    index.close()


def test_retry_reuses_source_based_mentions_without_duplicate_facts(tmp_path):
    kb, graph, index, _llm, _queue, ingest = setup_open(tmp_path)
    ingest.ingest_chunks([chunk(kb)], tenant_id="t")
    ids = {entity.id for entity in graph.list_entities()}
    ingest.ingest_chunks([chunk(kb)], tenant_id="t")
    assert {entity.id for entity in graph.list_entities()} == ids
    assert len(graph.list_relations()) == 1
    index.close()


def test_ambiguous_resolution_is_reviewed_while_entities_remain_separate(tmp_path):
    kb, graph, index, _llm, queue, ingest = setup_open(tmp_path, judge=False)
    ingest.ingest_chunks([chunk(kb)], tenant_id="t", batch_id="batch1")
    result = ingest.ingest_chunks([chunk(kb, doc_id="d2")], tenant_id="t", batch_id="batch2")
    assert result.conflicts_review == 2
    assert len(queue.list(type="resolution", tenant_id="t")) == 2
    assert len([e for e in graph.list_entities() if e.type == "人"]) == 2
    index.close()


@pytest.mark.parametrize(
    "failure", ["quote", "endpoint", "spoofed_source", "missing_hash", "wrong_scope"]
)
def test_invalid_open_output_or_source_never_enters_graph(tmp_path, failure):
    kb, graph, index, llm, _queue, ingest = setup_open(tmp_path)
    source = chunk(kb)
    if failure == "quote":
        llm.payload["entities"][0]["evidence"]["quote"] = "不存在"
    elif failure == "endpoint":
        llm.payload["relations"][0]["head"] = "unlisted"
    elif failure == "spoofed_source":
        llm.payload["entities"][0]["doc_id"] = "another-document"
    elif failure == "missing_hash":
        source.metadata.pop("content_sha256")
    else:
        source.kb_id = "another-kb"
    with pytest.raises(ValueError):
        ingest.ingest_chunks([source], tenant_id="t")
    assert graph.counts()["nodes"] == 0
    index.close()


def test_open_mode_without_capabilities_is_explicit_not_demo_rules(tmp_path):
    kb = KnowledgeBase(tenant_id="t", kb_id="open", build_mode="open")
    graph = ScopedGraphStore(InMemoryGraphStore(), ScopeBinding(kb.scope))
    ingest = GraphIngestor(GraphIngestDeps(graph=graph, knowledge_base=kb))
    result = ingest.ingest_chunks([chunk(kb)], tenant_id="t")
    assert result.error.startswith("open_capability_unavailable")
    assert graph.counts()["nodes"] == 0
    with pytest.raises(ValueError):
        ingest.ingest_chunks([chunk(kb)], tenant_id="other")
