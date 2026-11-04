"""Durable document operations inside the ingest worker (K-02…K-06).

Task rows carrying an ``op_id`` are executed by the operation runners instead of
the plain indexing path. Every poll first dispatches operations whose task was
never written (crash between the doc-store commit and the JSONL journal).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from agentic_graphrag.core.ports.document_ops import ACTIVE_OP_STATES, OpKind, OpState
from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore
from agentic_graphrag.core.ports.stores import DocStore
from agentic_graphrag.knowledge.chunk_op_runner import ChunkOpRunner
from agentic_graphrag.knowledge.document_commands import DocumentCommands
from agentic_graphrag.knowledge.index_pipeline import IndexPipeline
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.ingest_tasks import IngestTask, IngestTaskStore
from agentic_graphrag.knowledge.parse_op_runner import ParseOpRunner
from agentic_graphrag.knowledge.parsers import create_parser

logger = logging.getLogger(__name__)
RERUNNABLE_STATES = (*ACTIVE_OP_STATES, OpState.FAILED.value)


class WorkerOps:
    def __init__(
        self, docs: DocumentOpsStore, pipeline: IndexPipeline, tasks: IngestTaskStore | None
    ) -> None:
        self.docs = docs
        self.commands = DocumentCommands(docs, tasks)
        self.chunks = ChunkOpRunner(pipeline, docs)
        parser = create_parser(pipeline.config.parsing)
        self.parses = ParseOpRunner(pipeline, docs, parser=parser, tasks=tasks)

    @classmethod
    def build(
        cls, docs: DocStore, pipeline: IndexPipeline, tasks: IngestTaskStore | None
    ) -> WorkerOps | None:
        return cls(docs, pipeline, tasks) if isinstance(docs, DocumentOpsStore) else None

    def dispatch(self) -> int:
        dispatched = self.commands.dispatch_pending()
        if dispatched:
            logger.info("Dispatched %d pending document operation(s)", dispatched)
        return dispatched

    def run_task(
        self, task: IngestTask, *, heartbeat: Callable[[], None]
    ) -> list[IngestResult] | None:
        """Results for an operation task, or ``None`` for a plain indexing task."""
        op_ids = list(dict.fromkeys(row["op_id"] for row in task.docs if row.get("op_id")))
        if not op_ids:
            return None
        results: list[IngestResult] = []
        for op_id in op_ids:
            results.extend(self._run(op_id, heartbeat=heartbeat))
        return results

    def _run(self, op_id: str, *, heartbeat: Callable[[], None]) -> list[IngestResult]:
        op = self.docs.get_op(op_id)
        if op is None or op.state == OpState.DONE:
            return []
        if op.state not in RERUNNABLE_STATES:
            return [IngestResult(op.doc_id, error=op.error or op.state, fatal=True)]
        if op.kind == OpKind.CHUNK_PATCH:
            return [self.chunks.run(op, heartbeat=heartbeat)]
        if op.kind == OpKind.REPARSE:
            return self.parses.reparse(op, heartbeat=heartbeat)
        return self.parses.parse(op, heartbeat=heartbeat)
