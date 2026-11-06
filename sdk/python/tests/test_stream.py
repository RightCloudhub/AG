import asyncio
import json

import httpx
import pytest
from conftest import event

from ag_client import APIError, AsyncClient, Client, ProtocolError, StreamInterrupted
from ag_client.stream import MAX_EVENT_CHARS, SSEDecoder


class SyncStream(httpx.SyncByteStream):
    def __init__(self, frames, *, fail=False):
        self.frames, self.fail, self.closed = frames, fail, False

    def __iter__(self):
        yield from self.frames
        if self.fail:
            raise httpx.ReadError("socket lost")

    def close(self):
        self.closed = True


class AsyncStream(httpx.AsyncByteStream):
    def __init__(self, frames, *, fail=False, wait=False):
        self.frames, self.fail, self.closed, self.wait = frames, fail, False, wait
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for frame in self.frames:
            yield frame
        if self.fail:
            raise httpx.ReadError("socket lost")
        if self.wait:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def sse_response(stream):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)


def test_sync_stream_unified_result_unknown_events_and_raw_chain(answer):
    answer["schema_version"] = "1.0.0"
    stream = SyncStream(
        [
            b"\xef\xbb\xbf: heartbeat\n\n",
            b"event: future-event\ndata: not JSON\n\n",
            event("triage", {"query_id": "query-1"}, event_id="query-1:1"),
            event("answer", answer, event_id="query-1:2"),
        ]
    )
    with Client(transport=httpx.MockTransport(lambda _: sse_response(stream))) as ag:
        with ag.stream("q") as events:
            result = list(events)
    assert stream.closed
    assert [item.event for item in result] == ["triage", "answer"]
    assert result[0].result is None
    assert result[1].result.audit_status == "persisted"
    assert result[1].data["schema_version"] == "1.0.0"
    assert result[1].id == "query-1:2"


@pytest.mark.parametrize("fail", [False, True])
def test_sync_interruption_carries_query_id_without_post_replay(fail):
    calls = []
    stream = SyncStream([event("thinking", {"query_id": "query-1"})], fail=fail)

    def handle(request):
        calls.append(request)
        return sse_response(stream)

    with Client(transport=httpx.MockTransport(handle), read_retries=3) as ag:
        with pytest.raises(StreamInterrupted) as error, ag.stream("q") as events:
            list(events)
    assert stream.closed and len(calls) == 1
    assert error.value.query_id == "query-1"


def test_unterminated_answer_is_an_interrupted_stream(answer):
    stream = SyncStream([event("answer", answer).rstrip(b"\n")])
    with Client(transport=httpx.MockTransport(lambda _: sse_response(stream))) as ag:
        with pytest.raises(StreamInterrupted), ag.stream("q") as events:
            list(events)


def test_sync_break_closes_http_response():
    stream = SyncStream([event("thinking", {"query_id": "q"})], fail=True)
    with Client(transport=httpx.MockTransport(lambda _: sse_response(stream))) as ag:
        with ag.stream("q") as events:
            assert next(events).event == "thinking"
        assert stream.closed


def test_stream_error_is_a_typed_business_error():
    stream = SyncStream([event("error", {"code": "AUDIT_FAILED", "message": "Unavailable"})])
    with Client(transport=httpx.MockTransport(lambda _: sse_response(stream))) as ag:
        with pytest.raises(APIError) as error, ag.stream("q") as events:
            list(events)
    assert stream.closed and error.value.code == "AUDIT_FAILED"


@pytest.mark.parametrize("body", [b"[]", b"not json"])
def test_known_event_requires_json_object(body):
    stream = SyncStream([b"event: triage\ndata: " + body + b"\n\n"])
    with Client(transport=httpx.MockTransport(lambda _: sse_response(stream))) as ag:
        with pytest.raises(ProtocolError), ag.stream("q") as events:
            list(events)


def test_sse_decoder_limits_and_multiline_json():
    decoder = SSEDecoder()
    for line in ["id: good", "id: bad\x00", "event: thinking", 'data: {"query_id":', 'data: "q"}']:
        assert decoder.feed(line) is None
    frame = decoder.feed("")
    assert frame.id == "good" and frame.data["query_id"] == "q"
    with pytest.raises(ProtocolError, match="size limit"):
        decoder.feed("data: " + "a" * MAX_EVENT_CHARS)


def test_stream_http_error_and_wrong_content_type():
    responses = [
        httpx.Response(401, json={"success": False, "error": {"code": "UNAUTHORIZED"}}),
        httpx.Response(200, json={"oops": 1}),
    ]
    with Client(transport=httpx.MockTransport(lambda _: responses.pop(0))) as ag:
        with pytest.raises(APIError), ag.stream("q"):
            pass
        with pytest.raises(ProtocolError), ag.stream("q"):
            pass


async def test_async_stream_answer_and_resource_release(answer):
    stream = AsyncStream([event("answer", answer)])
    async with AsyncClient(transport=httpx.MockTransport(lambda _: sse_response(stream))) as ag:
        async with ag.stream("q") as events:
            received = [item async for item in events]
    assert stream.closed and received[0].result.answer == answer["answer"]


@pytest.mark.parametrize("fail", [False, True])
async def test_async_stream_interruption(fail):
    stream = AsyncStream([event("thinking", {"query_id": "query-1"})], fail=fail)
    async with AsyncClient(transport=httpx.MockTransport(lambda _: sse_response(stream))) as ag:
        with pytest.raises(StreamInterrupted) as error:
            async with ag.stream("q") as events:
                _ = [item async for item in events]
    assert stream.closed and error.value.query_id == "query-1"


async def test_async_cancellation_closes_stream_and_starts_no_second_request():
    stream = AsyncStream([event("thinking", {"query_id": "q"})], wait=True)
    calls = []

    def handle(request):
        calls.append(request)
        return sse_response(stream)

    async with AsyncClient(transport=httpx.MockTransport(handle)) as ag:

        async def consume():
            async with ag.stream("q") as events:
                return [item async for item in events]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(stream.waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed and len(calls) == 1


async def test_async_stream_rejects_http_failure_and_non_sse():
    responses = [
        httpx.Response(403, json={"success": False, "error": {"code": "FORBIDDEN"}}),
        httpx.Response(200, json={"oops": 1}),
    ]
    async with AsyncClient(transport=httpx.MockTransport(lambda _: responses.pop(0))) as ag:
        with pytest.raises(APIError):
            async with ag.stream("q"):
                pass
        with pytest.raises(ProtocolError):
            async with ag.stream("q"):
                pass


@pytest.mark.parametrize("cls", [Client, AsyncClient])
def test_credentials_not_in_client_repr(cls):
    client = cls(api_key="sensitive-placeholder")
    assert "sensitive-placeholder" not in repr(client)


def test_raw_chain_fields_are_not_dropped(answer):
    decoder = SSEDecoder()
    chain = {**answer, "metadata": {"audit_status": "persisted"}, "schema_version": "1.0.0"}
    for line in event("answer", chain).decode().splitlines():
        result = decoder.feed(line)
        if result:
            assert json.loads(json.dumps(result.data)) == chain
            assert result.result.model_extra["schema_version"] == "1.0.0"
