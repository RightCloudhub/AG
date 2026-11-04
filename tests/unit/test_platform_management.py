"""Management flows publish only validated generations and preserve human authority."""

import json

from test_retrieval_contract import A, B, add_document
from test_retrieval_contract import api as api

from agentic_graphrag.app.knowledge_scope import select_service
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.summary_queue import process_summary_work
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.core.knowledge import SourceRef
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.knowledge.communities import ConnectedComponentsDetector
from agentic_graphrag.llm.provider import MockLLMProvider


class SummaryModel(MockLLMProvider):
    def __init__(self, *, invalid=False):
        super().__init__()
        self.invalid = invalid

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        sources = json.loads(messages[-1].content)["sources"]
        return json.dumps(
            {
                "findings": [
                    {"text": row["text"], "source_ids": ["invented" if self.invalid else row["id"]]}
                    for row in sources
                ]
            }
        )


def test_summary_management_recovers_and_retries_without_reindexing(api, monkeypatch):
    client, svc = api
    add_document(svc)
    path = "/v1/knowledge-bases/one"
    result = client.patch(
        path, headers=A, json={"expected_revision": 1, "global_summary_enabled": True}
    )
    assert result.status_code == 200, result.text
    generation = result.json()["data"]["active_generation"]
    assert generation != "1"
    assert client.post(path + "/summary-index/build", headers=A, json={}).status_code == 503
    assert (
        client.post(
            path + "/summary-index/build",
            headers=A,
            json={"rebuild": True, "operation_id": "unavailable-rebuild"},
        ).status_code
        == 503
    )
    assert client.get(path, headers=A).json()["data"]["active_generation"] == generation
    svc.allow_llm = True
    monkeypatch.setattr(
        "agentic_graphrag.app.summary_communities.LeidenDetector",
        lambda **kw: ConnectedComponentsDetector(),
    )
    queued = client.post(path + "/summary-index/build", headers=A, json={})
    assert queued.json()["data"]["jobs"] == {"pending": 1}
    # A later process can claim jobs without replaying the document ingestion task.
    process_summary_work(
        svc, llm=SummaryModel(invalid=True), detector=ConnectedComponentsDetector()
    )
    jobs = client.get(path + "/summary-jobs", headers=A).json()["data"]
    assert len(jobs) == 1 and jobs[0]["status"] == "failed"
    assert (
        client.post(path + f"/summary-jobs/{jobs[0]['job_id']}/retry", headers=B).status_code == 404
    )
    assert client.post(path + f"/summary-jobs/{jobs[0]['job_id']}/retry", headers=A).is_success
    model = SummaryModel()
    process_summary_work(svc, llm=model, detector=ConnectedComponentsDetector())
    assert len(model.calls) == 1
    process_summary_work(svc, llm=model, detector=ConnectedComponentsDetector())
    assert len(model.calls) == 1
    status = client.get(path + "/summary-index", headers=A).json()["data"]
    assert status["status"] == "ready" and status["ready_documents"] == 1
    assert status["generation"] == generation


def test_schema_draft_requires_human_mapping_and_can_rollback(api):
    client, svc = api
    created = client.post(
        "/v1/knowledge-bases",
        headers=A,
        json={"kb_id": "open", "name": "Open", "build_mode": "open"},
    )
    assert created.is_success, created.text
    doc = add_document(svc, kb_id="open", text="小林负责星海项目。")
    bound = select_service(svc, tenant_id="alpha", kb_id="open")
    scope = bound.knowledge_base.scope
    source = SourceRef(
        scope=scope,
        doc_id=doc.doc_id,
        doc_version=1,
        chunk_id=doc.chunks[0].chunk_id,
        content_sha256=doc.content_sha256,
        span_start=0,
        span_end=len(doc.content),
    ).model_dump(mode="json")
    entities = [
        EntityRecord(key, name, kind, tenant_id="alpha", sources=[source])
        for key, name, kind in (("person", "小林", "负责人"), ("project", "星海项目", "项目"))
    ]
    bound.bundle.graph.upsert_entities(entities)
    bound.bundle.graph.upsert_relations(
        [
            RelationRecord(
                "relation",
                "person",
                "负责",
                "project",
                confidence=0.99,
                tenant_id="alpha",
                sources=[source],
                attributes={"graph_mode": "open"},
            )
        ]
    )
    base = "/v1/knowledge-bases/open"
    response = client.post(base + "/schema-drafts", headers=A, json={})
    assert response.is_success, response.text
    draft = response.json()["data"]
    path = base + f"/schema-drafts/{draft['draft_id']}"
    assert (
        client.post(path + "/promote", headers=A, json={"operation_id": "promote"}).status_code
        == 409
    )
    approval = {key: draft[key] for key in ("schema", "relation_mapping", "entity_mapping")}
    approval.update(schema_id="contracts", schema_version="1", reviewer="human")
    assert client.post(path + "/approve", headers=A, json=approval).is_success
    result = client.post(path + "/promote", headers=A, json={"operation_id": "promote"})
    assert result.is_success, result.text
    kb = result.json()["data"]
    assert kb["build_mode"] == "schema" and kb["rule_planner_profile"] is None
    again = client.post(path + "/promote", headers=A, json={"operation_id": "promote"})
    assert again.json()["data"]["active_generation"] == kb["active_generation"]
    rolled = client.post(
        base + "/rollback",
        headers=A,
        json={"generation": "1", "expected_revision": kb["config_revision"]},
    )
    assert rolled.is_success, rolled.text
    assert rolled.json()["data"]["build_mode"] == "open"
    assert client.get(path, headers=B).status_code == 404


def test_review_advice_is_shadow_and_calibration_uses_human_records(api, monkeypatch):
    client, svc = api
    path = "/v1/knowledge-bases/one"
    configured = client.patch(
        path, headers=A, json={"expected_revision": 1, "review_policy": "required"}
    )
    assert configured.is_success
    upload = client.post(
        "/v1/docs?kb_id=one",
        headers=A,
        files={"files": ("leadership.txt", b"Alice Stone is the CEO of Apex Holdings.")},
    )
    assert upload.is_success, upload.text
    build_inline_worker(svc).run_once()
    items = client.get("/v1/review-queue?kb_id=one", headers=A).json()["data"]
    item = next(row for row in items if row["type"] == "extraction")
    svc.allow_llm = True
    monkeypatch.setattr(
        QueryService,
        "_build_llm",
        lambda self, budget: MockLLMProvider(
            {"": json.dumps({"decision": "approve", "reason": "The original names the CEO."})},
            budget=budget,
        ),
    )
    advice = client.post(f"/v1/review-queue/{item['id']}/advice?kb_id=one", headers=A)
    assert advice.is_success, advice.text
    assert advice.json()["data"]["automation"] == "automation_disabled"
    still_pending = client.get("/v1/review-queue?kb_id=one", headers=A).json()["data"]
    assert any(row["id"] == item["id"] for row in still_pending)
    decided = client.post(
        f"/v1/review-queue/{item['id']}/decision?kb_id=one",
        headers=A,
        json={"decision": "approve", "reviewer": "human"},
    )
    assert decided.is_success, decided.text
    calibration = client.post(
        "/v1/review-calibration?kb_id=one",
        headers=A,
        json={"dataset_id": "synthetic", "review_ids": [item["id"]]},
    )
    assert calibration.is_success, calibration.text
    assert calibration.json()["data"]["reviewed"] == 1
    assert calibration.json()["data"]["real_corpus"] is False
    duplicate = client.post(
        "/v1/review-calibration?kb_id=one",
        headers=A,
        json={"dataset_id": "synthetic", "review_ids": [item["id"], item["id"]]},
    )
    assert duplicate.status_code == 409
