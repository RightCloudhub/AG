import json

import httpx
import pytest
from conftest import envelope

from ag_client import APIError, AsyncClient, ProtocolError, QueryAudit, SearchAudit, TransportError


async def test_async_operations(answer, evidence, search_result):
    requests = []

    async def handle(request):
        requests.append(request)
        path = request.url.path
        if "knowledge-bases" in path:
            return envelope([{"kb_id": "contracts"}], total=1)
        if "evidence" in path:
            return envelope(evidence)
        return envelope(search_result if "search" in path else answer)

    async with AsyncClient(
        kb_id="contracts", api_key="k", transport=httpx.MockTransport(handle)
    ) as ag:
        assert (await ag.query("q", audit_required=True)).audit_status == "persisted"
        assert (await ag.search("s", top_k=2)).search_id == "search-1"
        assert (await ag.get_evidence("e")).source_refs[0].doc_version == 2
        assert isinstance(await ag.get_audit("q"), QueryAudit)
        assert isinstance(await ag.get_audit("s", kind="search"), SearchAudit)
        assert [kb.kb_id async for kb in ag.iter_knowledge_bases()] == ["contracts"]
        with pytest.raises(ValueError):
            await ag.get_audit("q", kind="oops")
    assert requests[0].headers["authorization"] == "Bearer k"
    assert json.loads(requests[0].content)["audit_required"] is True


async def test_async_retries_only_reads(evidence):
    count = 0

    async def handle(request):
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ConnectError("no connection")
        if request.method == "POST":
            raise httpx.ReadTimeout("response lost")
        return envelope(evidence)

    async with AsyncClient(transport=httpx.MockTransport(handle)) as ag:
        await ag.get_evidence("e")
        with pytest.raises(TransportError):
            await ag.query("q")
    assert count == 3


async def test_async_status_retry_and_envelope_error(evidence):
    count = 0

    async def handle(request):
        nonlocal count
        count += 1
        if count == 2:
            return envelope(evidence)
        return httpx.Response(503, json={"success": False, "error": {"code": "BUSY"}})

    async with AsyncClient(transport=httpx.MockTransport(handle)) as ag:
        await ag.get_evidence("e")
        with pytest.raises(APIError) as error:
            await ag.search("q")
    assert error.value.code == "BUSY"
    assert count == 3


async def test_async_pagination_must_advance():
    async with AsyncClient(
        transport=httpx.MockTransport(lambda _: envelope([{"kb_id": "a"}]))
    ) as ag:
        with pytest.raises(ProtocolError):
            _ = [kb async for kb in ag.iter_knowledge_bases(page_size=1)]
