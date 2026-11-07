"""Original citation identities survive failed resolution and deletion races."""

import pytest
from test_retrieval_contract import A, add_document
from test_retrieval_contract import api as api

from agentic_graphrag.app.evidence_sources import query_scope, source_store
from agentic_graphrag.app.knowledge_scope import select_service
from agentic_graphrag.core.evidence import Candidate, CandidateSource, Citation


@pytest.mark.parametrize("kb_id", ["default", "one"])
@pytest.mark.parametrize("delete_before_response", [True, False])
@pytest.mark.parametrize("legacy_audit", [True, False])
def test_unresolved_search_citations_are_rechecked(
    api, monkeypatch, kb_id, delete_before_response, legacy_audit
):
    client, svc = api
    doc = add_document(svc, kb_id=kb_id)
    selected = select_service(svc, tenant_id="alpha", kb_id=kb_id)
    hit = Candidate(
        id="retrieved",
        content=doc.content,
        source=CandidateSource.FULLTEXT_CHUNK,
        citations=[Citation(doc_id=doc.doc_id, chunk_id="same-chunk", doc_version=1)],
    )
    # Resolution can fail even without deletion, e.g. the chunk is no longer present.
    doc.chunks = []
    selected.bundle.docs.save(doc)
    monkeypatch.setattr(
        "agentic_graphrag.app.search_service.run_tool_specs", lambda *_a, **_k: ([[hit]], [])
    )
    if delete_before_response:
        assert client.delete(
            f"/v1/documents/{doc.doc_id}", headers=A, params={"kb_id": kb_id}
        ).is_success
    response = client.post("/v1/search", headers=A, json={"kb_id": kb_id, "query": "payment"})
    assert response.is_success, response.text
    data = response.json()["data"]
    candidate = data["candidates"][0]
    assert candidate["source_refs"] == []
    if legacy_audit:
        # Older search rows can recover identities from their evidence artifact.
        candidate.pop("citations")
        data["search_id"] += "-legacy"
        source_store(selected).put(
            query_scope(selected), data, kind="search", key=data["search_id"]
        )
    if delete_before_response:
        assert candidate["source_status"] == "deleted"
        assert candidate["content"] == ""
        stored = source_store(selected).get(query_scope(selected), data["search_id"], kind="search")
        assert stored["candidates"][0]["content"] == ""
    else:
        assert candidate["content"] == doc.content
        assert client.delete(
            f"/v1/documents/{doc.doc_id}", headers=A, params={"kb_id": kb_id}
        ).is_success
    audit = client.get(
        f"/v1/audit/searches/{data['search_id']}", headers=A, params={"kb_id": kb_id}
    )
    assert audit.is_success, audit.text
    assert audit.json()["data"]["candidates"][0]["source_status"] == "deleted"
    assert "Confidential" not in audit.text


def test_citationless_search_preserves_optional_audit_failure_policy(api, monkeypatch):
    from agentic_graphrag.governance.source_store import SourceStore

    client, _ = api
    hit = Candidate(
        id="unattributed", content="A graph fact", source=CandidateSource.GRAPH_NEIGHBOR
    )
    monkeypatch.setattr(
        "agentic_graphrag.app.search_service.run_tool_specs", lambda *_a, **_k: ([[hit]], [])
    )

    def unavailable(*_args, **_kwargs):
        raise OSError("archive unavailable")

    monkeypatch.setattr(SourceStore, "put_many", unavailable)
    monkeypatch.setattr(SourceStore, "get", unavailable)
    response = client.post("/v1/search", headers=A, json={"kb_id": "one", "query": "fact"})
    assert response.is_success, response.text
    assert response.json()["data"]["audit_status"] == "failed"
    assert response.json()["data"]["candidates"][0]["content"] == hit.content
