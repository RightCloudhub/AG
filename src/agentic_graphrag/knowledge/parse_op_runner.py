"""Worker side of async parse and reparse operations (K-02/K-03).

Parsing (possibly a slow MinerU call) runs outside every lock into a staged
operation record. Publication then rechecks version, lifecycle intent, chunk
mutation sequence and the lease fence under the document lock; any change is a
publication conflict and the serving version stays untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentic_graphrag.core.ports.document_ops import DocOperation, OpState, SourceArtifact
from agentic_graphrag.core.ports.document_ops_store import DocumentOpsStore
from agentic_graphrag.core.ports.parsing import DocumentParser
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.index_pipeline import IndexPipeline
from agentic_graphrag.knowledge.index_result import IngestResult
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore

PUBLICATION_CONFLICT = "publication_conflict"


def _parse_error(exc: Exception) -> str:
    return f"parse_failed: {type(exc).__name__}: {exc}"[:500]


class ParseOpRunner:
    def __init__(
        self,
        pipeline: IndexPipeline,
        docs: DocumentOpsStore,
        *,
        parser: DocumentParser,
        tasks: IngestTaskStore | None,
    ) -> None:
        self.pipeline, self.docs, self.parser, self.tasks = pipeline, docs, parser, tasks

    def reparse(self, op: DocOperation, *, heartbeat: Callable[[], None]) -> list[IngestResult]:
        if "published_version" not in op.payload:
            failed = self._publish_reparse(op, heartbeat)
            if failed is not None:
                return [failed]
        result = self.pipeline.process(op.doc_id, tenant_id=op.tenant_id, heartbeat=heartbeat)
        self.docs.update_op(op.op_id, state=OpState.DONE.value, error=result.error or "")
        return [result]

    def _publish_reparse(
        self, op: DocOperation, heartbeat: Callable[[], None]
    ) -> IngestResult | None:
        fence = self.pipeline.acquire(op.tenant_id, op.doc_id)
        assert fence is not None
        try:
            self.docs.update_op(op.op_id, state=OpState.RUNNING.value)
            try:
                parsed = self._parse_for_reparse(op)
            except Exception as exc:  # noqa: BLE001 — recorded on the operation and task
                return self._failed(op, _parse_error(exc))
            self.docs.stage_content(op.op_id, (op.tenant_id, op.doc_id), parsed)
            heartbeat()
            with self.docs.lock():
                doc = self._publishable(op, fence_ok=self.docs.fence_valid(fence))
                if doc is None:
                    return self._failed(op, PUBLICATION_CONFLICT, state=OpState.CONFLICT)
                replacement = self.pipeline.lifecycle.force_replace(
                    doc, content=parsed[0], metadata=parsed[1]
                )
                self._carry_source(op, replacement)
                payload = {**op.payload, "published_version": replacement.version}
                self.docs.update_op(op.op_id, payload=payload)
        finally:
            self.pipeline.release(fence)
        return None

    def _parse_for_reparse(self, op: DocOperation) -> tuple[str, dict[str, Any]]:
        doc = self.docs.get(op.doc_id, tenant_id=op.tenant_id)
        if doc is None:
            raise LookupError("Document no longer exists")
        if op.payload.get("mode") == "rechunk":
            return doc.content, {"reparsed_from": "normalized_text"}
        source = self.docs.source_for(
            (op.tenant_id, op.doc_id, int(op.payload["expected_version"]))
        )
        if source is None:
            raise LookupError("Retained source is no longer available")
        data = self.docs.artifacts.read(op.tenant_id, source.sha256)
        parsed = self.parser.parse(data, filename=source.filename or doc.title)
        return parsed.markdown, {**parsed.metadata(), "reparsed_from": "source"}

    def _publishable(self, op: DocOperation, *, fence_ok: bool) -> DocumentRecord | None:
        doc = self.docs.get(op.doc_id, tenant_id=op.tenant_id)
        unchanged = (
            fence_ok
            and doc is not None
            and not doc.metadata.get("lifecycle")
            and doc.version == int(op.payload.get("expected_version", -1))
            and self.docs.mutation_seq(op.tenant_id, op.doc_id)
            == int(op.payload.get("mutation_seq", 0))
        )
        return doc if unchanged else None

    def _carry_source(self, op: DocOperation, replacement: DocumentRecord) -> None:
        previous = self.docs.source_for((op.tenant_id, op.doc_id, replacement.version - 1))
        if previous is not None:
            self.docs.record_source((op.tenant_id, op.doc_id, replacement.version), previous)

    def _failed(
        self, op: DocOperation, error: str, *, state: OpState = OpState.FAILED
    ) -> IngestResult:
        self.docs.update_op(op.op_id, state=state.value, error=error)
        return IngestResult(op.doc_id, error=error, fatal=True)

    def parse(self, op: DocOperation, *, heartbeat: Callable[[], None]) -> list[IngestResult]:
        """Async upload: parse every file, then accept them all, then index."""
        files: list[dict[str, Any]] = list(op.payload.get("files") or [])
        rows: list[dict[str, str]] = list(op.payload.get("accepted") or [])
        if not rows:
            failed = self._publish_parse(op, files, heartbeat)
            if isinstance(failed, IngestResult):
                return [failed]
            rows = failed
        results = [
            self.pipeline.process(r["doc_id"], tenant_id=op.tenant_id, heartbeat=heartbeat)
            for r in rows
            if r["status"] != "duplicate" or self._unfinished(op.tenant_id, r["doc_id"])
        ]
        self.docs.update_op(op.op_id, state=OpState.DONE.value)
        return results

    def _unfinished(self, tenant_id: str, doc_id: str) -> bool:
        doc = self.docs.get(doc_id, tenant_id=tenant_id)
        return doc is not None and self.pipeline.needs_processing(doc)

    def _publish_parse(
        self, op: DocOperation, files: list[dict[str, Any]], heartbeat: Callable[[], None]
    ) -> IngestResult | list[dict[str, str]]:
        self.docs.update_op(op.op_id, state=OpState.RUNNING.value)
        self.docs.set_reservations(op.op_id, state="running")
        parsed, errors = self._parse_files(op, files, heartbeat)
        if errors:
            message = "; ".join(f"{name}: {err}" for name, err in errors.items())
            self.docs.set_reservations(op.op_id, state="failed", error=message[:500])
            self._task_rows(op, [{**_row(f), "error": errors.get(f["name"], "")} for f in files])
            return self._failed(op, message[:500])
        with self.docs.lock():
            rows = [self._accept(op, f, doc) for f, doc in zip(files, parsed, strict=True)]
            self.docs.update_op(op.op_id, payload={**op.payload, "accepted": rows})
        self._task_rows(op, rows)
        self.docs.set_reservations(op.op_id, state="published")
        return rows

    def _parse_files(
        self, op: DocOperation, files: list[dict[str, Any]], heartbeat: Callable[[], None]
    ) -> tuple[list[DocumentRecord], dict[str, str]]:
        parsed: list[DocumentRecord] = []
        errors: dict[str, str] = {}
        for f in files:
            heartbeat()
            try:
                data = self.docs.artifacts.read(op.tenant_id, str(f["sha256"]))
                result = self.parser.parse(data, filename=str(f["name"]))
            except Exception as exc:  # noqa: BLE001 — every failure lands on the task
                errors[str(f["name"])] = _parse_error(exc)
                continue
            parsed.append(_record(op, f, markdown=result.markdown, metadata=result.metadata()))
        return parsed, errors

    def _accept(
        self, op: DocOperation, file: dict[str, Any], doc: DocumentRecord
    ) -> dict[str, str]:
        row = self.pipeline.lifecycle.accept(doc, reserved_id=str(file["doc_id"]))
        if row["status"] != "duplicate":
            artifact = SourceArtifact(
                str(file["sha256"]),
                str(file.get("media_type") or ""),
                str(file["name"]),
                int(file.get("size") or 0),
            )
            self.docs.record_source((op.tenant_id, row["doc_id"], int(row["version"])), artifact)
        return {**row, "op_id": op.op_id, "kind": op.kind, "provisional_doc_id": file["doc_id"]}

    def _task_rows(self, op: DocOperation, rows: list[dict[str, str]]) -> None:
        if self.tasks is not None:
            self.tasks.update_docs(op.task_id, rows)


def _row(file: dict[str, Any]) -> dict[str, str]:
    return {"doc_id": str(file["doc_id"]), "name": str(file["name"]), "status": "parse_failed"}


def _record(
    op: DocOperation, file: dict[str, Any], *, markdown: str, metadata: dict[str, str]
) -> DocumentRecord:
    return DocumentRecord(
        doc_id="",
        title=str(file["name"]),
        content=markdown,
        metadata={
            "task_id": op.task_id,
            "source": "upload",
            "bytes": int(file.get("size") or 0),
            "tenant_id": op.tenant_id,
            **metadata,
        },
        tenant_id=op.tenant_id,
        source_uri=str(file["source_uri"]),
    )
