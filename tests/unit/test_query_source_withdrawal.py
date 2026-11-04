"""Concurrent document deletion withdraws evidence before HTTP/SSE publication."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_retrieval_contract import A, add_document
from test_retrieval_contract import api as api

from agentic_graphrag.app import query_run, search_service
from agentic_graphrag.app.wiring import MIN_COST_UNITS


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("audit_required", [False, True])
@pytest.mark.parametrize("kb_id", ["one", "default"])
def test_inflight_answer_checks_tombstones_and_settles_budget(
    api, monkeypatch, stream, audit_required, kb_id
):
    client, svc = api
    doc = add_document(svc, kb_id=kb_id, text="Alice Stone is the CEO of Apex Holdings.")
    ready, resume = Event(), Event()
    generated = []
    persist = query_run.persist_chain

    def paused(selected, chain):
        generated.append(chain.model_dump(mode="json"))
        ready.set()
        assert resume.wait(10), "test did not release publication"
        persist(selected, chain)

    monkeypatch.setattr(query_run, "persist_chain", paused)
    path = "/v1/query/stream" if stream else "/v1/query"
    body = {
        "kb_id": kb_id,
        "question": "Who is the CEO of Apex Holdings?",
        "force_agentic": True,
        "audit_required": audit_required,
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, path, headers=A, json=body)
        try:
            assert ready.wait(10), "query did not reach publication"
            assert "Alice Stone" in json.dumps(generated)
            assert client.delete(
                f"/v1/documents/{doc.doc_id}", headers=A, params={"kb_id": kb_id}
            ).is_success
        finally:
            resume.set()
        response = future.result(timeout=10)
    if stream:
        terminal = [
            frame
            for frame in response.text.split("\n\n")
            if "\nevent: answer\n" in frame or "\nevent: error\n" in frame
        ]
        assert len(terminal) == 1
        assert ("event: error" if audit_required else "event: answer") in terminal[0]
        assert "Alice Stone" not in terminal[0]
    else:
        assert response.status_code == (503 if audit_required else 200), response.text
        assert "Alice Stone" not in response.text
    if audit_required:
        assert "AUDIT_UNAVAILABLE" in response.text
    else:
        assert '"source_retracted":true' in response.text.replace(" ", "")
        assert '"status":"no_answer"' in response.text.replace(" ", "")
    usage = svc.multi_budget.snapshot(tenant_id="alpha")["tenants"]["alpha"]
    assert usage["llm_calls"] == 0 and usage["cost_units"] == MIN_COST_UNITS


def test_inflight_search_withdraws_candidate_content_and_preserves_audit(api, monkeypatch):
    client, svc = api
    doc = add_document(svc)
    ready, resume = Event(), Event()
    finalize = search_service._finalize_search

    def paused(selected, result, *, audit_required):
        ready.set()
        assert resume.wait(10)
        return finalize(selected, result, audit_required=audit_required)

    monkeypatch.setattr(search_service, "_finalize_search", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post,
            "/v1/search",
            headers=A,
            json={"kb_id": "one", "query": "payment", "audit_required": True},
        )
        try:
            assert ready.wait(10)
            assert client.delete(
                f"/v1/documents/{doc.doc_id}", headers=A, params={"kb_id": "one"}
            ).is_success
        finally:
            resume.set()
        response = future.result(timeout=10)
    assert response.is_success, response.text
    data = response.json()["data"]
    assert data["audit_status"] == "persisted"
    assert data["candidates"][0]["source_status"] == "deleted"
    assert "Confidential" not in response.text
