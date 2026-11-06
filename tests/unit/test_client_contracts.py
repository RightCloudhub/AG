"""Real FastAPI contracts are consumed by the independent SDK and MCP adapter."""

import httpx
import pytest
from ag_client import APIError, AsyncClient, Client, QueryRequest
from test_retrieval_contract import add_document
from test_retrieval_contract import api as api


class TestAppTransport(httpx.BaseTransport):
    __test__ = False

    def __init__(self, app_client):
        self.app_client = app_client

    def handle_request(self, request):
        response = self.app_client.request(
            request.method,
            request.url.path,
            params=request.url.params,
            headers=request.headers,
            content=request.read(),
        )
        return httpx.Response(
            response.status_code, headers=response.headers, content=response.content
        )


def test_sync_sdk_query_search_stream_evidence_and_audit_use_real_app(api):
    app_client, svc = api
    add_document(svc)
    with Client(api_key="alpha-key", kb_id="one", transport=TestAppTransport(app_client)) as client:
        assert {kb.kb_id for kb in client.iter_knowledge_bases(page_size=1)} >= {"one", "two"}
        search = client.search("payment", audit_required=True)
        assert search.audit_status == "persisted"
        evidence = client.get_evidence(search.candidates[0].id, limit=12)
        assert evidence.status == "available" and evidence.has_more
        assert client.get_audit(search.search_id, kind="search").search_id == search.search_id
        query = client.query("hello", audit_required=True)
        assert query.audit_status == "persisted"
        assert client.get_audit(query.query_id).query_id == query.query_id
        with client.stream(
            QueryRequest(question="hello", kb_id="one", audit_required=True)
        ) as events:
            frames = list(events)
        assert frames[-1].result.audit_status == "persisted"
        assert len({frame.data["query_id"] for frame in frames}) == 1
        with pytest.raises(APIError) as unknown:
            client.get_evidence(search.candidates[0].id, kb_id="two")
        assert unknown.value.status_code == 404


async def test_async_sdk_and_mcp_five_tools_use_real_api_and_scoped_receipts(api):
    pytest.importorskip("mcp")
    from ag_mcp.adapter import ToolAdapter
    from ag_mcp.contracts import tool_definitions

    app_client, svc = api
    add_document(svc)
    async with AsyncClient(
        api_key="alpha-key", kb_id="one", transport=httpx.ASGITransport(app=app_client.app)
    ) as client:
        adapter = ToolAdapter(client, kb_id="one")
        assert len(tool_definitions()) == 5
        listed = await adapter.call("list_knowledge_bases", {})
        assert {kb["kb_id"] for kb in listed["items"]} >= {"one", "two"}
        found = await adapter.call("search", {"query": "payment"})
        assert found["audit_status"] == "persisted"
        original = await adapter.call("get_evidence", {"evidence_id": found["candidates"][0]["id"]})
        assert original["content"] == "Confidential payment terms."
        audit = await adapter.call("get_audit", {"audit_id": found["search_id"], "kind": "search"})
        assert audit["record"]["search_id"] == found["search_id"]
        result = await adapter.call("query", {"question": "hello"})
        assert result["audit_status"] == "persisted"
        audit = await adapter.call("get_audit", {"audit_id": result["query_id"], "kind": "query"})
        assert audit["record"]["query_id"] == result["query_id"]
        forbidden = await adapter.call("search", {"query": "payment", "tenant_id": "beta"})
        assert forbidden.isError


async def test_official_mcp_session_discovers_calls_and_resolves_actual_api_evidence(api):
    pytest.importorskip("mcp")
    from ag_mcp.server import build_server
    from mcp.shared.memory import create_connected_server_and_client_session

    app_client, svc = api
    add_document(svc)
    async with AsyncClient(
        api_key="alpha-key", kb_id="one", transport=httpx.ASGITransport(app=app_client.app)
    ) as client:
        async with create_connected_server_and_client_session(
            build_server(client, kb_id="one")
        ) as session:
            definitions = await session.list_tools()
            assert {tool.name for tool in definitions.tools} == {
                "query",
                "search",
                "list_knowledge_bases",
                "get_evidence",
                "get_audit",
            }
            search = await session.call_tool("search", {"query": "payment"})
            assert not search.isError and search.structuredContent["audit_status"] == "persisted"
            evidence_id = search.structuredContent["candidates"][0]["id"]
            evidence = await session.call_tool("get_evidence", {"evidence_id": evidence_id})
            assert not evidence.isError
            assert evidence.structuredContent["content"] == "Confidential payment terms."
            audit = await session.call_tool(
                "get_audit", {"audit_id": search.structuredContent["search_id"], "kind": "search"}
            )
            assert (
                not audit.isError
                and audit.structuredContent["record"]["audit_status"] == "persisted"
            )
            denied = await session.call_tool(
                "get_evidence", {"evidence_id": evidence_id, "kb_id": "two"}
            )
            assert denied.isError
