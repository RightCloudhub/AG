import asyncio
import sys
import tempfile
from pathlib import Path

import anyio
import httpx
import pytest
from ag_client import AsyncClient
from fake_api import FakeAPI
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import CancelledNotification, CancelledNotificationParams, ClientNotification

from ag_mcp.server import build_server


async def test_official_client_initialize_discovery_and_schema_validation():
    api = FakeAPI()
    async with AsyncClient(api_key="test-key", transport=httpx.MockTransport(api.handle)) as client:
        server = build_server(client, kb_id="contracts")
        async with create_client_server_memory_streams() as (client_io, server_io):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(server.run, *server_io, server.create_initialization_options())
                async with ClientSession(*client_io) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "agentic-graphrag"
                    assert len((await session.list_tools()).tools) == 5
                    result = await session.call_tool("query", {"question": "Contract risks?"})
                    assert result.isError is False
                    assert result.structuredContent["audit_status"] == "persisted"
                    rejected = await session.call_tool(
                        "query", {"question": "q", "tenant_id": "victim"}
                    )
                    assert rejected.isError is True
                    assert len(api.requests) == 1
                tasks.cancel_scope.cancel()


async def test_actual_stdio_roundtrip_search_evidence_and_audit():
    fixture = Path(__file__).with_name("stdio_fixture.py")
    params = StdioServerParameters(command=sys.executable, args=[str(fixture)])
    with tempfile.TemporaryFile(mode="w+") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.version == "0.1.0"
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {
                    "list_knowledge_bases",
                    "search",
                    "query",
                    "get_evidence",
                    "get_audit",
                }
                kbs = await session.call_tool("list_knowledge_bases", {})
                assert kbs.structuredContent["items"][0]["kb_id"] == "contracts"
                search = await session.call_tool("search", {"query": "liability"})
                assert not search.isError and search.structuredContent["search_id"] == "search-1"
                evidence = await session.call_tool("get_evidence", {"evidence_id": "ev-1"})
                assert evidence.structuredContent["source_refs"][0]["doc_id"] == "contract-1"
                audit = await session.call_tool(
                    "get_audit", {"audit_id": "search-1", "kind": "search"}
                )
                assert audit.structuredContent["record"]["audit_status"] == "persisted"
                query = await session.call_tool("query", {"question": "Contract risks?"})
                assert not query.isError and query.structuredContent["query_id"] == "query-1"
        errlog.seek(0)
        assert "Traceback" not in errlog.read()


async def test_official_client_cancellation_reaches_the_http_operation():
    started, cancelled = asyncio.Event(), asyncio.Event()
    request_id = None

    async def handle(request):
        nonlocal request_id
        request_id = server.request_context.request_id
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with AsyncClient(transport=httpx.MockTransport(handle)) as client:
        server = build_server(client)
        async with create_client_server_memory_streams() as (client_io, server_io):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(server.run, *server_io, server.create_initialization_options())
                async with ClientSession(*client_io) as session:
                    await session.initialize()
                    query = asyncio.create_task(session.call_tool("query", {"question": "q"}))
                    await asyncio.wait_for(started.wait(), timeout=2)
                    await session.send_notification(
                        ClientNotification(
                            CancelledNotification(
                                params=CancelledNotificationParams(
                                    requestId=request_id, reason="test"
                                )
                            )
                        )
                    )
                    await asyncio.wait_for(cancelled.wait(), timeout=2)
                    with pytest.raises(McpError, match="cancel"):
                        await query
                tasks.cancel_scope.cancel()
