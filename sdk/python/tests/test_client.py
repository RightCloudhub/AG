import json

import httpx
import pytest
from conftest import envelope
from pydantic import ValidationError

from ag_client import (
    APIError,
    Client,
    ProtocolError,
    QueryAudit,
    QueryRequest,
    SearchAudit,
    SearchRequest,
    TransportError,
)
from ag_client.transport import ClientSettings


def test_query_and_scoped_search(answer, search_result):
    seen = []

    def handle(request):
        seen.append(request)
        return envelope(answer if request.url.path.endswith("query") else search_result)

    with Client(api_key="test-key", kb_id="contracts", transport=httpx.MockTransport(handle)) as ag:
        result = ag.query("Contract risks?", audit_required=True)
        assert result.audit_status == "persisted"
        assert result.cost.tokens == 12
        ag.query(QueryRequest(question="q", kb_id="explicit", strategy="global"))
        hits = ag.search("liability", top_k=3, audit_required=True)
        assert hits.snapshot.scope.kb_id == "contracts"
        ag.search(SearchRequest(query="q", kb_id="explicit"), kb_id="override", top_k=2)
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert "tenant_id" not in json.loads(seen[0].content)
    assert json.loads(seen[0].content)["kb_id"] == "contracts"
    assert json.loads(seen[1].content)["kb_id"] == "explicit"
    assert json.loads(seen[2].content)["top_k"] == 3
    assert json.loads(seen[3].content)["kb_id"] == "override"


def test_audit_kinds_evidence_and_query_extra_fields(answer, search_result, evidence):
    answer["future_field"] = {"v": 1}
    seen = []

    def handle(request):
        seen.append(request)
        if "/evidence/" in request.url.path:
            return envelope(evidence)
        return envelope(search_result if "/searches/" in request.url.path else answer)

    with Client(kb_id="contracts", transport=httpx.MockTransport(handle)) as ag:
        query = ag.get_audit("query-1")
        assert isinstance(query, QueryAudit)
        assert query.model_extra["future_field"] == {"v": 1}
        assert isinstance(ag.get_audit("search-1", kind="search"), SearchAudit)
        source = ag.get_evidence("ev-1", kb_id="b", offset=5, limit=10)
        assert source.source_refs[0].doc_version == 2
        assert source.has_more
        with pytest.raises(ValueError):
            ag.get_audit("q", kind="arbitrary/path")
        with pytest.raises(ValueError):
            ag.get_evidence("e", offset=-1)
        with pytest.raises(ValueError):
            ag.get_evidence("e", limit=20_000)
        with pytest.raises(ValueError):
            ag.get_evidence("")
    assert seen[1].url.path == "/v1/audit/searches/search-1"
    assert dict(seen[2].url.params) == {"kb_id": "b", "offset": "5", "limit": "10"}


def test_pagination_and_duplicate_page_detection():
    def handle(request):
        page = int(request.url.params["page"])
        return envelope([{"kb_id": f"kb-{page}"}], total=2)

    with Client(transport=httpx.MockTransport(handle)) as ag:
        assert [kb.kb_id for kb in ag.iter_knowledge_bases(page_size=1)] == ["kb-1", "kb-2"]
        with pytest.raises(ValueError):
            ag.list_knowledge_bases(page=0)
        with pytest.raises(ValueError):
            ag.list_knowledge_bases(limit=0)
    with Client(transport=httpx.MockTransport(lambda _: envelope([{"kb_id": "a"}]))) as ag:
        with pytest.raises(ProtocolError, match="did not advance"):
            list(ag.iter_knowledge_bases(page_size=1))


def test_only_get_retries_and_closes_transport(evidence):
    calls = []

    def handle(request):
        calls.append(request.method)
        if len(calls) == 1 or request.method == "POST":
            return httpx.Response(503, json={"success": False, "error": {"code": "BUSY"}})
        return envelope(evidence)

    transport = httpx.MockTransport(handle)
    with Client(transport=transport) as ag:
        assert ag.get_evidence("e").evidence_id == "ev-1"
        with pytest.raises(APIError) as error:
            ag.query("q")
        assert error.value.code == "BUSY"
    assert calls == ["GET", "GET", "POST"]


def test_network_errors_are_typed_and_paid_post_not_retried():
    calls = []

    def fail(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("do not copy arbitrary transport exception")

    with Client(transport=httpx.MockTransport(fail)) as ag:
        with pytest.raises(TransportError, match="no response"):
            ag.get_evidence("e")
        with pytest.raises(TransportError) as exc:
            ag.search("q")
        assert "arbitrary" not in str(exc.value)
    assert calls == ["GET", "GET", "POST"]


@pytest.mark.parametrize(
    "response,kind",
    [
        (httpx.Response(200, text="not JSON"), ProtocolError),
        (httpx.Response(502, text="secret upstream detail"), APIError),
        (httpx.Response(200, json={"data": {}}), ProtocolError),
        (httpx.Response(200, json={"success": True, "data": {}, "meta": []}), ProtocolError),
        (httpx.Response(200, json={"success": True, "data": None}), ProtocolError),
        (envelope({"wrong": "result"}), ProtocolError),
    ],
)
def test_invalid_wire_responses(response, kind):
    with Client(transport=httpx.MockTransport(lambda _: response), read_retries=0) as ag:
        with pytest.raises(kind):
            ag.query("q")


def test_business_errors_even_on_200_have_request_id():
    response = httpx.Response(
        200,
        json={
            "success": False,
            "error": {"code": "AUDIT_FAILED", "message": "Audit unavailable", "details": {"x": 1}},
            "meta": {"request_id": "r1"},
        },
    )
    with Client(transport=httpx.MockTransport(lambda _: response)) as ag:
        with pytest.raises(APIError) as error:
            ag.query("q")
    assert error.value.status_code == 200
    assert error.value.request_id == "r1"
    assert error.value.details == {"x": 1}


@pytest.mark.parametrize(
    "url", ["file:///etc", "http://user:password@localhost", "http://host?q=1", "http://host#x"]
)
def test_invalid_base_urls(url):
    with pytest.raises(ValueError):
        Client(url)


def test_input_validation():
    with pytest.raises(ValueError):
        Client(read_retries=4)
    with pytest.raises(ValueError):
        Client(timeout=0)
    with pytest.raises(ValidationError):
        QueryRequest(question=" ")
    with pytest.raises(ValidationError):
        QueryRequest(question="q", strategy="global", force_agentic=True)
    with pytest.raises(ValidationError):
        SearchRequest(query="q", top_k=51)
    assert (
        ClientSettings("https://example.com/prefix/").url("/v1/query")
        == "https://example.com/prefix/v1/query"
    )
