"""An in-memory HTTP boundary used by the actual stdio protocol fixture."""

import json

import httpx

ANSWER = {
    "query_id": "query-1",
    "question": "Contract risks?",
    "answer": "Uncapped liability.",
    "status": "answered",
    "route": "agentic",
    "claims": [],
    "cost": {"tokens": 10},
    "metadata": {"audit_status": "persisted", "kb_id": "contracts"},
}
SEARCH = {
    "search_id": "search-1",
    "audit_status": "persisted",
    "kb_id": "contracts",
    "candidates": [{"id": "ev-1", "content": "Uncapped liability.", "source": "fulltext_chunk"}],
}
EVIDENCE = {
    "evidence_id": "ev-1",
    "status": "available",
    "content": "Uncapped liability.",
    "source_refs": [{"doc_id": "contract-1", "doc_version": 1, "chunk_id": "chunk-1"}],
    "total_chars": 19,
}


class FakeAPI:
    def __init__(self):
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST":
            assert json.loads(request.content)["audit_required"] is True
            assert request.headers["authorization"] == "Bearer test-key"
        if path == "/v1/knowledge-bases":
            return response(
                [{"kb_id": "contracts", "name": "Contracts", "build_mode": "open"}], total=1
            )
        if path.startswith("/v1/evidence/"):
            return response(EVIDENCE)
        if "search" in path:
            return response(SEARCH)
        return response(ANSWER)


def response(data, **meta):
    return httpx.Response(200, json={"success": True, "data": data, "error": None, "meta": meta})
