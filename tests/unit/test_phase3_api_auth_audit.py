"""P3/P4 API: auth, audit, stream, feedback, metrics."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.auth import parse_api_keys
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.api.sse import format_sse
from agentic_graphrag.generation.audit_store import AuditStore
from agentic_graphrag.generation.trace import ReasoningChain


def test_parse_api_keys():
    m = parse_api_keys("acme:secret1,beta:secret2")
    assert m["secret1"] == "acme"
    assert m["secret2"] == "beta"


def test_format_sse():
    s = format_sse("triage", {"route": "agentic"})
    assert "event: triage" in s
    assert "data:" in s


def test_audit_store_roundtrip(tmp_path):
    store = AuditStore(tmp_path / "audit.jsonl")
    chain = ReasoningChain(question="q?", answer="a")
    qid = store.save(chain)
    row = store.get(qid)
    assert row is not None
    assert row["question"] == "q?"


def test_api_query_and_audit():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)

    r = client.get("/healthz")
    assert r.status_code == 200

    r = client.post("/v1/query", json={"question": "Who is the CEO of Apex Holdings?"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["answer"]
    assert data["query_id"]
    assert data["route"] in {"agentic", "fast_path"}

    # audit
    r2 = client.get(f"/v1/audit/queries/{data['query_id']}")
    assert r2.status_code == 200
    assert r2.json()["data"]["query_id"] == data["query_id"]

    # feedback
    r3 = client.post(
        "/v1/feedback",
        json={"query_id": data["query_id"], "accurate": False, "reason": "test"},
    )
    assert r3.status_code == 200

    # metrics
    r4 = client.get("/v1/metrics")
    assert r4.status_code == 200
    assert "count" in r4.json()["data"]

    svc.close()


def test_api_stream():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/query/stream",
        json={"question": "Who is the CEO of Apex Holdings?"},
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "event:" in text
    assert "answer" in text
    svc.close()


def test_web_ui_served():
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    r = client.get("/web")
    # may be 200 if web/ present
    assert r.status_code in {200, 404}
    svc.close()


def test_critic_partial_is_clean_entity():
    from agentic_graphrag.agent.critic import extract_entity_conclusion
    from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource

    c = Candidate(
        id="1",
        source=CandidateSource.GRAPH_NEIGHBOR,
        content="Apex Holdings -[PARENT_OF]-> BrightLink Logistics (Company)",
        structured={
            "kind": "neighbor",
            "head": "Apex Holdings",
            "tail": "BrightLink Logistics",
            "relation": "PARENT_OF",
            "neighbor": "BrightLink Logistics",
            "query_entity": "BrightLink Logistics",
        },
    )
    name = extract_entity_conclusion(
        "What is the parent company of BrightLink Logistics?", [c]
    )
    assert name == "Apex Holdings"
    assert "-[" not in (name or "")
