"""Refactor §0-B/§0-D: core kernel, legacy aliases, and event-driven read side."""

from __future__ import annotations

import ast
from pathlib import Path

from agentic_graphrag.core.events import (
    DomainEvent,
    GraphCommitted,
    IndexUpdated,
    InProcessEventBus,
    RecordingEventBus,
    ReviewDecided,
)

CORE = Path(__file__).resolve().parents[2] / "src" / "agentic_graphrag" / "core"


def test_legacy_import_paths_alias_core_modules() -> None:
    import agentic_graphrag.core.evidence as ev
    import agentic_graphrag.core.ports.llm as llm_port
    import agentic_graphrag.core.ports.stores as st
    import agentic_graphrag.core.reasoning as rs
    import agentic_graphrag.generation.trace as old_rs
    import agentic_graphrag.llm.interfaces as old_llm
    import agentic_graphrag.retrieval.contracts as old_ev
    import agentic_graphrag.stores.interfaces as old_st

    assert old_ev is ev and old_rs is rs and old_st is st and old_llm is llm_port
    from agentic_graphrag.llm.provider import Message, Tier

    assert Message is llm_port.Message and Tier is llm_port.Tier


def test_core_depends_on_nothing_else_in_the_project() -> None:
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if mod.startswith("agentic_graphrag."):
                    assert mod.startswith("agentic_graphrag.core"), f"{path.name} imports {mod}"


def test_bus_dispatches_by_type_and_isolates_handler_errors() -> None:
    bus = InProcessEventBus()
    got: list[str] = []

    def boom(_e):
        raise RuntimeError("x")

    bus.subscribe(GraphCommitted, boom)
    bus.subscribe(GraphCommitted, lambda e: got.append(f"g:{e.source}"))
    bus.subscribe(DomainEvent, lambda e: got.append(e.name))
    bus.publish(GraphCommitted(source="ingest"))
    bus.publish(ReviewDecided(review_id="r1"))
    assert got == ["g:ingest", "GraphCommitted", "ReviewDecided"]


def test_graph_commit_invalidates_cache_and_refreshes_lexicon() -> None:
    from agentic_graphrag.api.service import QueryService
    from agentic_graphrag.api.service_ingest import events_for
    from agentic_graphrag.core.ports.stores import EntityRecord

    svc = QueryService.create_offline()
    rec = RecordingEventBus()
    svc.events = rec
    svc._read_side_subscribed = False
    bus = events_for(svc)
    before = svc.retrieval_cache.index_version.current()
    svc.bundle.graph.upsert_entities([EntityRecord(id="zz", name="Zyxcorp", type="Company")])
    bus.publish(GraphCommitted(source="test"))
    assert svc.retrieval_cache.index_version.current() == before + 1
    assert any(isinstance(e, IndexUpdated) for e in rec.events)
    assert "Zyxcorp" in svc.known_entities
    svc.close()
