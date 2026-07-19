"""P2-ARCH-03 — POST /v1/query + envelope + schema validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.envelope import fail, ok
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.config import get_config, get_settings


@pytest.fixture(scope="module")
def client() -> TestClient:
    svc = QueryService.create_offline(
        seed_triples="data/processed/seed_triples.jsonl",
        cfg=get_config(),
        settings=get_settings(),
    )
    app = create_app(query_service=svc)
    with TestClient(app) as c:
        yield c
    svc.close()


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_query_success_envelope(client: TestClient) -> None:
    r = client.post(
        "/v1/query",
        json={
            "question": "Who is the CEO of the parent company of NovaTech Industries?",
            "max_hops": 4,
            "force_agentic": True,
        },
        headers={"x-request-id": "test-req-1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["meta"]["request_id"] == "test-req-1"
    data = body["data"]
    assert data["question"]
    assert "answer" in data
    assert data["status"] in {"answered", "partial", "no_answer"}
    assert "query_id" in data
    assert "cost" in data
    assert "latency_ms" in data["cost"]


def test_query_invalid_empty_question(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "   "})
    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_INPUT"
    assert "internal" not in body["error"]["message"].lower()


def test_query_invalid_missing_question(client: TestClient) -> None:
    r = client.post("/v1/query", json={})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_INPUT"


def test_query_invalid_max_hops(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "Who is CEO?", "max_hops": 0})
    assert r.status_code == 422
    assert r.json()["success"] is False


def test_query_question_too_long(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "x" * 2001})
    assert r.status_code == 422


def test_query_request_schema_strip() -> None:
    req = QueryRequest(question="  hello  ")
    assert req.question == "hello"


def test_envelope_helpers() -> None:
    assert ok({"a": 1})["success"] is True
    bad = fail("X", "msg", details={"k": 1})
    assert bad["success"] is False
    assert bad["error"]["code"] == "X"
