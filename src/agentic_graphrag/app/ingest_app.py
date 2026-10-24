"""IngestApp: the write-side use cases shared by API, CLI and workers (refactor §0-A)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


from agentic_graphrag.app.write_path import (
    build_graph_ingestor,
    build_inline_worker,
    events_for,
    start_inline_worker,
)
from agentic_graphrag.core.events import EventBus
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.knowledge.graph_ingest import GraphIngestResult
from agentic_graphrag.knowledge.ingest_worker import IngestWorker


class IngestApp:
    def __init__(self, svc: QueryService) -> None:
        self._svc = svc

    @property
    def events(self) -> EventBus:
        return events_for(self._svc)

    def ingest_chunks(
        self, chunks: list[ChunkRecord], *, tenant_id: str = "", batch_id: str = ""
    ) -> GraphIngestResult:
        """Extract → gate → resolve → commit one document's chunks into the graph."""
        return build_graph_ingestor(self._svc).ingest_chunks(
            chunks, tenant_id=tenant_id, batch_id=batch_id
        )

    def build_worker(self) -> IngestWorker:
        return build_inline_worker(self._svc)

    def start_inline_worker(self) -> IngestWorker | None:
        return start_inline_worker(self._svc)
