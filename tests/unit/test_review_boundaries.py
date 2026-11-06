"""Regression probes for publication, dictionary output and configuration boundaries."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_knowledge_workflow import root_service as root_service

from agentic_graphrag.api.routes import knowledge_bases, retrieval
from agentic_graphrag.app.audit_reads import visible_query_audit
from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.evidence_sources import source_store
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.query_persistence import persist_chain
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.llm.output_format import output_format


def test_unresolved_deleted_citation_withdraws_answer_and_archived_text(root_service, monkeypatch):
    svc = select_service(root_service, tenant_id="default", kb_id="default")
    catalog = [
        {
            "id": "hit",
            "content": "Deleted secret",
            "citations": [
                {"doc_id": "gone", "chunk_id": "chunk", "doc_version": 1},
            ],
        }
    ]
    row = {"answer": "Deleted secret", "metadata": {"evidence": deepcopy(catalog)}}
    knowledge_store(svc).tombstone(svc.knowledge_base.scope, "gone", through_version=1)
    chain = ReasoningChain(
        query_id="withdraw", question="Q", answer="Deleted secret", metadata={"evidence": catalog}
    )
    persist_chain(svc, chain)
    assert chain.metadata.get("source_retracted") is True
    assert "Deleted secret" not in chain.model_dump_json()
    assert "Deleted secret" not in str(visible_query_audit(svc, row))

    # Missing citation resolution must retain enough lineage for later archive reads.
    from agentic_graphrag.app.evidence_sources import publish_evidence

    evidence = publish_evidence(svc, catalog[0], scope=svc.knowledge_base.scope)
    archived = source_store(svc).get(
        svc.knowledge_base.scope, evidence["evidence_id"], kind="evidence"
    )
    assert archived["citations"] == catalog[0]["citations"]
    monkeypatch.setattr(retrieval, "knowledge_service", lambda request: svc)
    result = retrieval.get_evidence(evidence["evidence_id"], None, offset=0, limit=100)
    assert result["data"]["status"] == "deleted"
    assert result["data"]["content"] == ""


@pytest.mark.parametrize("additional", [True, {"type": "string"}])
def test_dictionary_schema_uses_compatible_output_mode(additional):
    schema = {
        "type": "object",
        "properties": {
            "args": {
                "type": "object",
                "additionalProperties": additional,
            }
        },
    }
    original = deepcopy(schema)
    assert output_format("json_schema", schema) == {"type": "json_object"}
    assert schema == original


def test_real_executor_and_triple_schemas_keep_dictionary_values():
    from agentic_graphrag.agent.executor import ExecutorPlan
    from agentic_graphrag.knowledge.schema_check import Triple

    for model in (ExecutorPlan, Triple):
        assert output_format("json_schema", model.model_json_schema()) == {"type": "json_object"}


def test_closed_schema_preserves_a_field_named_default():
    schema = {"type": "object", "properties": {"default": {"type": "string", "default": "x"}}}
    result = output_format("json_schema", schema)["json_schema"]["schema"]
    assert result["properties"] == {"default": {"type": "string"}}
    assert result["required"] == ["default"]


def test_unsupported_required_open_review_is_rejected_on_create_and_update(root_service):
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(query_service=root_service)),
        state=SimpleNamespace(),
    )
    body = knowledge_bases.KnowledgeCreate(
        kb_id="open", name="Open", build_mode="open", review_policy="required"
    )
    with pytest.raises(ApiError, match="Schema"):
        knowledge_bases.create_knowledge_base(body, request)
    assert knowledge_store(root_service).get(KnowledgeBase(kb_id="open").scope) is None
    kb = knowledge_store(root_service).create(KnowledgeBase(kb_id="open", build_mode="open"))
    update = knowledge_bases.KnowledgeUpdate(expected_revision=1, review_policy="required")
    with pytest.raises(ApiError, match="Schema"):
        knowledge_bases.update_knowledge_base(kb.kb_id, update, request)
    assert knowledge_store(root_service).get(kb.scope) == kb
