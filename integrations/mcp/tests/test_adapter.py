import asyncio
import json

import httpx
import pytest
from ag_client import AsyncClient
from fake_api import ANSWER, FakeAPI, response
from mcp.types import CallToolResult

from ag_mcp.adapter import ToolAdapter
from ag_mcp.contracts import tool_definitions


async def test_five_tools_use_only_the_authenticated_http_boundary():
    api = FakeAPI()
    async with AsyncClient(api_key="test-key", transport=httpx.MockTransport(api.handle)) as client:
        adapter = ToolAdapter(client, kb_id="contracts")
        kbs = await adapter.call("list_knowledge_bases", {})
        assert kbs["items"][0]["kb_id"] == "contracts"
        query = await adapter.call("query", {"question": "Contract risks?", "strategy": "global"})
        assert query["audit_status"] == "persisted"
        search = await adapter.call("search", {"query": "liability", "top_k": 2})
        assert search["search_id"] == "search-1"
        evidence = await adapter.call("get_evidence", {"evidence_id": "ev-1", "limit": 100})
        assert evidence["source_refs"][0]["doc_version"] == 1
        audit = await adapter.call("get_audit", {"audit_id": "query-1"})
        assert audit["kind"] == "query" and audit["record"]["query_id"] == "query-1"
        audit = await adapter.call("get_audit", {"audit_id": "search-1", "kind": "search"})
        assert audit["record"]["search_id"] == "search-1"
    assert json.loads(api.requests[1].content)["kb_id"] == "contracts"
    assert json.loads(api.requests[1].content)["audit_required"] is True
    assert json.loads(api.requests[2].content)["audit_required"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "victim"),
        ("api_key", "another-key"),
        ("base_url", "http://other"),
        ("audit_required", False),
        ("unknown", True),
    ],
)
async def test_identity_audit_and_endpoints_cannot_be_overridden(field, value):
    api = FakeAPI()
    async with AsyncClient(transport=httpx.MockTransport(api.handle)) as client:
        result = await ToolAdapter(client).call("query", {"question": "q", field: value})
    assert isinstance(result, CallToolResult) and result.isError
    assert result.structuredContent["error"]["code"] == "INVALID_INPUT"
    assert not api.requests


@pytest.mark.parametrize("status", ["failed", "disabled", None])
async def test_legacy_or_failed_audit_cannot_look_like_auditable_success(status):
    answer = {**ANSWER, "metadata": {"audit_status": status}}
    async with AsyncClient(transport=httpx.MockTransport(lambda _: response(answer))) as client:
        result = await ToolAdapter(client).call("query", {"question": "q"})
    assert result.isError and result.structuredContent["error"]["code"] == "AUDIT_NOT_PERSISTED"
    assert "query-1" in result.content[0].text


async def test_business_and_transport_errors_are_tool_errors():
    responses = [
        httpx.Response(
            403, json={"success": False, "error": {"code": "FORBIDDEN", "message": "Denied"}}
        ),
        httpx.Response(200, text="invalid envelope"),
    ]
    async with AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0))) as client:
        adapter = ToolAdapter(client)
        forbidden = await adapter.call("query", {"question": "q"})
        invalid = await adapter.call("query", {"question": "q"})
        unknown = await adapter.call("unknown", {})
    assert forbidden.structuredContent["error"]["code"] == "FORBIDDEN"
    assert invalid.structuredContent["error"]["code"] == "AG_UNAVAILABLE"
    assert unknown.structuredContent["error"]["code"] == "UNKNOWN_TOOL"


async def test_unexpected_failure_does_not_expose_exception_text(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("secret connection detail")

    async with AsyncClient(transport=httpx.MockTransport(FakeAPI().handle)) as client:
        monkeypatch.setattr(client, "query", fail)
        result = await ToolAdapter(client).call("query", {"question": "q"})
    assert result.isError and "secret connection detail" not in result.content[0].text


async def test_cancelled_tool_does_not_turn_cancellation_into_a_success_or_error():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handle(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with AsyncClient(transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(ToolAdapter(client).call("query", {"question": "q"}))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()


def test_schemas_and_read_only_annotations():
    tools = {tool.name: tool for tool in tool_definitions()}
    assert len(tools) == 5
    for name, tool in tools.items():
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.outputSchema["type"] == "object"
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is (name not in {"query", "search"})
