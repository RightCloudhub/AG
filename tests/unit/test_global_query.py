"""Global queries cover selected source documents and share REST/SSE behavior."""

import hashlib
import json

import pytest

from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.knowledge import KnowledgeBase, SourceRef, SummaryFinding, SummaryRecord
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.stores.factory import create_offline_bundle


class SummaryLLM(MockLLMProvider):
    def complete(self, messages, **kwargs):
        self.budget.record_call(10, 5)
        self.calls.append(messages)
        prompt = messages[0].content
        data = json.loads(messages[-1].content)
        if prompt.startswith("Check one proposed"):
            return json.dumps({"supported": True, "reason": "Explicit original text"})
        inputs = data["findings"]
        return json.dumps(
            {
                "findings": [
                    {"text": item["text"], "source_ids": [item["id"]], "importance": 0.9}
                    for item in inputs
                ]
            }
        )


@pytest.fixture
def summary_service(tmp_path, monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path))
    cfg = AppConfig()
    svc = QueryService(
        cfg=cfg, settings=Settings(), bundle=create_offline_bundle(cfg=cfg), allow_llm=True
    )
    store = knowledge_store(svc)
    kb = store.create(
        KnowledgeBase(kb_id="contracts", name="Contracts", global_summary_enabled=True)
    )
    scoped = select_service(svc, tenant_id="default", kb_id=kb.kb_id)
    for key, text in (
        ("a", "Contract A has unlimited liability."),
        ("b", "Contract B allows early termination."),
    ):
        digest = hashlib.sha256(text.encode()).hexdigest()
        chunk = ChunkRecord(
            chunk_id=key, doc_id=key, index=0, text=text, kb_id=kb.kb_id, fulltext_indexed=True
        )
        scoped.bundle.docs.save(
            DocumentRecord(
                doc_id=key,
                title=key,
                content=text,
                chunks=[chunk],
                kb_id=kb.kb_id,
                content_sha256=digest,
            )
        )
        ref = SourceRef(
            scope=kb.scope,
            doc_id=key,
            doc_version=1,
            chunk_id=key,
            content_sha256=digest,
            span_end=len(text),
        )
        store.save_summary(
            SummaryRecord(
                summary_id=key,
                scope=kb.scope,
                generation="1",
                kind="document",
                findings=(SummaryFinding(text=text, sources=(ref,)),),
                document_ids=(key,),
                input_fingerprint=digest,
                model_revision="test",
                prompt_version="test",
                status="ready",
            )
        )
    clients = []

    def build(**kwargs):
        client = SummaryLLM(budget=kwargs["budget"])
        clients.append(client)
        return client

    monkeypatch.setattr("agentic_graphrag.app.query_run.build_llm_for_service", build)
    monkeypatch.setattr("agentic_graphrag.app.query_stream.build_llm_for_service", build)
    yield svc, clients
    svc.close()


@pytest.mark.parametrize("streaming", [False, True])
def test_global_strategy_uses_summary_reports_and_originals(summary_service, streaming):
    svc, clients = summary_service
    req = QueryRequest(question="Summarize the main risks", kb_id="contracts", strategy="global")
    if streaming:
        events = list(svc.stream_query_events(req))
        assert any(kind == "global_progress" for kind, _ in events)
        assert sum(kind in {"answer", "error"} for kind, _ in events) == 1
        result = events[-1][1]
    else:
        result = svc.run_query(req).model_dump(mode="json")
    assert result["status"] == "answered"
    assert result["route"] == "agentic"
    assert result["metadata"]["strategy"] == "global"
    assert result["metadata"]["coverage"]["processed_documents"] == 2
    assert "unlimited liability" in result["answer"]
    assert clients[0].calls


def test_global_document_subset_never_reads_unselected_report(summary_service):
    svc, clients = summary_service
    result = svc.run_query(QueryRequest(question="概括主要风险", kb_id="contracts", doc_ids=["a"]))
    assert result.metadata["coverage"]["total_documents"] == 1
    assert not result.metadata["coverage"]["partial"]
    assert "early termination" not in result.answer
    assert all("early termination" not in str(messages) for messages in clients[0].calls)
    assert all(
        ref["doc_id"] == "a" for row in result.metadata["evidence"] for ref in row["source_refs"]
    )


def test_explicit_global_requires_enabled_capability(summary_service):
    svc, _ = summary_service
    with pytest.raises(ApiError) as unavailable:
        svc.run_query(QueryRequest(question="Summarize", strategy="global"))
    assert unavailable.value.code == "CAPABILITY_UNAVAILABLE"
    svc.allow_llm = False
    with pytest.raises(ApiError):
        svc.run_query(QueryRequest(question="Summarize", kb_id="contracts", strategy="global"))


def test_global_map_budget_is_reported_as_partial(summary_service):
    svc, _ = summary_service
    svc.cfg.summaries.max_map_units = 1
    result = svc.run_query(QueryRequest(question="Summarize", kb_id="contracts", strategy="global"))
    assert result.status == "partial"
    assert result.metadata["coverage"]["partial"]
    assert result.metadata["coverage"]["processed_documents"] == 1
    assert result.missing_info == ["b"]
