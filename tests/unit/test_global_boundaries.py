"""Global claims retain exact originals within selection and budget boundaries."""

import hashlib

import pytest
from test_global_query import summary_service as summary_service

from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef, SummaryFinding


def run(svc, *, docs=None):
    return svc.run_query(
        QueryRequest(question="Summarize risks", kb_id="contracts", strategy="global", doc_ids=docs)
    )


def replace_report(svc, **changes):
    store = knowledge_store(svc)
    scope = KnowledgeScope(kb_id="contracts")
    original = store.summaries(scope, generation="1")[0]
    # A separate report models corrupted/mislabelled index input without overwriting an artifact.
    changed = original.model_copy(update={"summary_id": "additional", **changes})
    store.save_summary(changed)
    return changed


@pytest.mark.parametrize("kind", ["wrong_document", "wrong_scope", "stale_version"])
def test_summary_sources_are_checked_before_sending_text_to_models(summary_service, kind):
    svc, clients = summary_service
    scope = KnowledgeScope(kb_id="contracts")
    ref = knowledge_store(svc).summaries(scope, generation="1")[1].findings[0].sources[0]
    if kind == "wrong_scope":
        ref = ref.model_copy(update={"doc_id": "a", "scope": KnowledgeScope(kb_id="secret")})
    elif kind == "stale_version":
        ref = ref.model_copy(update={"doc_id": "a", "doc_version": 2})
    replace_report(
        svc,
        document_ids=("a",),
        findings=(SummaryFinding(text="Secret outside selected inputs", sources=(ref,)),),
    )
    if kind == "wrong_scope":
        with pytest.raises(ApiError):
            run(svc, docs=["a"])
        assert not clients[0].calls
        return
    result = run(svc, docs=["a"])
    assert result.status == "answered"
    assert all("Secret outside" not in str(call) for call in clients[0].calls)


def test_small_budget_preserves_original_verification(summary_service):
    svc, clients = summary_service
    svc.cfg.summaries.query_max_llm_calls = 3
    result = run(svc)
    assert result.status == "partial"
    assert result.claims
    assert result.cost.llm_calls <= 3
    assert any("Check one proposed" in str(call) for call in clients[0].calls)
    assert len(result.metadata["prompt_versions"]) == 3


def test_partial_summary_contributes_without_claiming_complete_coverage(summary_service):
    svc, _ = summary_service
    store = knowledge_store(svc)
    scope = KnowledgeScope(kb_id="contracts")
    original = store.summaries(scope, generation="1")[0]
    # A new generation is the durable replacement boundary for summary artifacts.
    store.save_summary(original.model_copy(update={"summary_id": "0-partial", "status": "partial"}))
    svc.cfg.summaries.max_map_units = 1
    result = run(svc, docs=["a"])
    assert result.claims
    assert result.status == "partial"
    assert result.metadata["coverage"]["processed_documents"] == 0


def test_source_local_percentage_is_verified_and_exact_span_is_preserved(summary_service):
    svc, _ = summary_service
    scoped = select_service(svc, tenant_id="default", kb_id="contracts")
    doc = scoped.bundle.docs.get("a")
    text = "Private preamble. Late payments incur a 10% penalty. Other conditions."
    doc.content = doc.chunks[0].text = text
    doc.content_sha256 = hashlib.sha256(text.encode()).hexdigest()
    scoped.bundle.docs.save(doc)
    clause = "Late payments incur a 10% penalty."
    start = text.index(clause)
    ref = SourceRef(
        scope=scoped.knowledge_base.scope,
        doc_id="a",
        doc_version=1,
        chunk_id="a",
        content_sha256=doc.content_sha256,
        span_start=start,
        span_end=start + len(clause),
    )
    replace_report(
        svc, document_ids=("a",), findings=(SummaryFinding(text=clause, sources=(ref,)),)
    )
    result = run(svc, docs=["a"])
    assert clause in result.answer
    row = result.metadata["evidence"][0]
    assert row["source_refs"] == [ref.model_dump(mode="json")]
    assert "Private preamble" not in row["content"]


def test_large_report_is_split_into_bounded_map_units(summary_service):
    svc, clients = summary_service
    svc.cfg.summaries.source_tokens_per_call = 256
    scope = KnowledgeScope(kb_id="contracts")
    finding = knowledge_store(svc).summaries(scope, generation="1")[0].findings[0]
    replace_report(svc, document_ids=("a",), findings=tuple([finding] * 80))
    result = run(svc, docs=["a"])
    assert result.metadata["coverage"]["total_map_units"] > 2
    assert clients[0].calls
