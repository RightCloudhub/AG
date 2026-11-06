"""Summary lineage, bounded input and incremental reuse through the application service."""

import json
from dataclasses import replace

import pytest

from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.summary_index import build_summary_index
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.generation.global_verifier import original_excerpt
from agentic_graphrag.knowledge.chunk_sources import stamp_chunk_sources
from agentic_graphrag.knowledge.communities import ConnectedComponentsDetector
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.llm.provider import MockLLMProvider


class EchoSummary(MockLLMProvider):
    def __init__(self, *, invalid=False):
        super().__init__()
        self.invalid = invalid

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        inputs = json.loads(messages[-1].content)["sources"]
        return json.dumps(
            {
                "findings": [
                    {
                        "text": item["text"][:100],
                        "source_ids": ["invented" if self.invalid else item["id"]],
                    }
                    for item in inputs
                ]
            }
        )


@pytest.fixture
def summary_project(tmp_path, monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path / "runtime"))
    cfg = AppConfig()
    cfg.paths.cache_dir = str(tmp_path / "cache")
    cfg.summaries.source_tokens_per_call = 256
    svc = QueryService.create_offline(cfg=cfg, settings=Settings(), seed_triples=tmp_path / "empty")
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="reports", global_summary_enabled=True))
    bound = select_service(svc, tenant_id="default", kb_id=kb.kb_id)
    for key, text in (("a", "Unlimited liability applies."), ("b", "Early termination applies.")):
        doc = DocumentRecord(key, key, text, kb_id=kb.kb_id, metadata={"index_complete": True})
        doc.chunks = [ChunkRecord(key, key, text, 0, kb_id=kb.kb_id, fulltext_indexed=True)]
        stamp_chunk_sources(doc)
        bound.bundle.docs.save(doc)
    yield svc, kb
    svc.close()


def build(svc, kb, llm):
    return build_summary_index(
        svc, tenant_id=kb.tenant_id, kb_id=kb.kb_id, llm=llm, detector=ConnectedComponentsDetector()
    )


def test_document_reports_cover_graphless_docs_and_resolve_originals(summary_project):
    svc, kb = summary_project
    llm = EchoSummary()
    assert build(svc, kb, llm) == {"generated": 2, "reused": 0, "failed": 0}
    assert len(llm.calls) == 2
    bound = select_service(svc, tenant_id="default", kb_id=kb.kb_id)
    summaries = svc.knowledge_store.summaries(kb.scope, generation="1")
    assert {r.document_ids for r in summaries} == {("a",), ("b",)}
    for report in summaries:
        for finding in report.findings:
            assert (
                original_excerpt(finding.sources[0], bound.bundle.docs, scope=kb.scope)
                == finding.text
            )
    assert build(svc, kb, llm) == {"generated": 0, "reused": 0, "failed": 0}
    assert len(llm.calls) == 2


def test_incremental_generation_reuses_unchanged_report(summary_project):
    svc, kb = summary_project
    llm = EchoSummary()
    build(svc, kb, llm)
    pipeline = GenerationPipeline(svc.bundle, svc.knowledge_store)
    write = pipeline.prepare(kb.scope, operation_id="change")
    doc = write.bundle.docs.get("a", tenant_id="default")
    text = "Liability is now capped."
    changed = replace(doc, version=2, content=text, chunks=[replace(doc.chunks[0], text=text)])
    stamp_chunk_sources(changed)
    write.bundle.docs.save(changed)
    published = pipeline.complete(write)
    assert build(svc, published, llm) == {"generated": 1, "reused": 1, "failed": 0}
    assert len(llm.calls) == 3
    before = svc.knowledge_store.summaries(kb.scope, generation="1")
    after = svc.knowledge_store.summaries(kb.scope, generation=published.active_generation)
    original = next(row for row in before if row.document_ids == ("b",))
    reused = next(row for row in after if row.document_ids == ("b",))
    assert (
        original.findings == reused.findings
        and original.input_fingerprint == reused.input_fingerprint
    )


def test_invalid_generated_citations_fail_jobs_without_publishing(summary_project):
    svc, kb = summary_project
    counts = build(svc, kb, EchoSummary(invalid=True))
    assert counts["failed"] == 2
    assert svc.knowledge_store.summaries(kb.scope, generation="1") == []
