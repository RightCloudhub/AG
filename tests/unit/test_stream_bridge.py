"""SSE terminal, cancellation and event-loop behavior without a network service."""

import asyncio
import json
import threading
import time

from test_knowledge_workflow import root_service as root_service

from agentic_graphrag.api.stream_bridge import StreamBridge


async def test_cached_stream_uses_bridge_identity_for_frames_and_audit(root_service):
    from agentic_graphrag.app.answer_cache import cached_chain
    from agentic_graphrag.app.contracts import QueryRequest
    from agentic_graphrag.app.query_persistence import persist_chain
    from agentic_graphrag.core.reasoning import ReasoningChain

    svc = root_service
    svc.enable_cache = True
    original = ReasoningChain(query_id="original", question="hello", answer="hello")
    persist_chain(svc, original)
    svc.retrieval_cache.set_answer(
        "hello", original.model_dump(mode="json"), tenant_id="default", user_id="anonymous"
    )

    def events():
        chain = cached_chain(
            svc, QueryRequest(question="hello"), tenant_id="default", user_id="anonymous"
        )
        assert chain is not None
        yield "cache_hit", {"query_id": chain.query_id}
        yield "answer", chain.model_dump(mode="json")

    bridge = StreamBridge(events)
    frames = [frame async for frame in bridge.frames()]
    assert len(frames) == 2
    for frame in frames:
        assert f"id: {bridge.query_id}:" in frame
        data = json.loads(
            next(line[6:] for line in frame.splitlines() if line.startswith("data: "))
        )
        assert data["query_id"] == bridge.query_id
    assert svc.audit_store.get_for_tenant(bridge.query_id, "default") is not None
    await asyncio.to_thread(bridge.thread.join, 1)


async def test_terminal_is_unique_and_blocking_work_does_not_block_asgi():
    def events():
        time.sleep(0.05)
        yield "thinking", {"message": "working"}
        yield "answer", {"answer": "done"}
        raise AssertionError("A terminal event must stop consumption")

    bridge = StreamBridge(events)
    frames = bridge.frames()
    first = asyncio.create_task(anext(frames))
    await asyncio.sleep(0.01)
    assert not first.done()
    output = [await first, *[frame async for frame in frames]]
    assert len(output) == 2
    assert "event: answer" in output[-1]
    assert all(f"id: {bridge.query_id}:" in frame for frame in output)
    await asyncio.to_thread(bridge.thread.join, 1)
    assert not bridge.thread.is_alive()


async def test_disconnect_closes_source_before_starting_another_operation():
    closed = threading.Event()
    calls = []

    def events():
        try:
            calls.append("first")
            yield "thinking", {}
            calls.append("second")
            yield "answer", {}
        finally:
            closed.set()

    bridge = StreamBridge(events)
    frames = bridge.frames()
    await anext(frames)
    await frames.aclose()
    await asyncio.to_thread(bridge.thread.join, 1)
    assert closed.is_set()
    assert calls == ["first"]


async def test_empty_stream_and_source_failure_have_one_safe_error():
    def broken():
        raise RuntimeError("private/internal/path")

    for factory in (lambda: iter(()), broken):
        bridge = StreamBridge(factory)
        frames = [frame async for frame in bridge.frames()]
        assert len(frames) == 1
        assert "event: error" in frames[0]
        assert "private/internal/path" not in frames[0]
        await asyncio.to_thread(bridge.thread.join, 1)
