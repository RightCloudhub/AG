"""Retained sources, async parse and reparse through the worker (K-02/K-03)."""

import pytest
from docops_support import CONTENT, make_env

from agentic_graphrag.core.enums import IngestStatus
from agentic_graphrag.core.ports.document_ops import (
    DocumentQuery,
    ReparseUnavailable,
    RevisionConflict,
    SourceArtifact,
)
from agentic_graphrag.core.ports.parsing import DocumentParseError
from agentic_graphrag.knowledge.document_commands import InvalidCommand, StagedFile


def respond(op):
    return {"task_id": op.task_id, "doc_id": op.doc_id}


def with_source(env, content=CONTENT):
    did = env.upload(content)
    sha = env.docs.artifacts.put("alpha", content.encode())
    env.docs.record_source(("alpha", did, 1), SourceArtifact(sha, "text/markdown", "guide.md", 9))
    return did


def reparse(env, did, *, key="r", version=1, mode="source"):
    return env.commands.request_reparse(
        ("alpha", did), expected_version=version, mode=mode, key=key, respond=respond
    )


def test_unchanged_text_reparse_publishes_a_new_version_and_rebuilds(tmp_path):
    env = make_env(tmp_path)
    did = with_source(env)
    outcome = reparse(env, did)
    assert reparse(env, did).response == outcome.response  # idempotent retry
    assert env.docs.get(did, tenant_id="alpha").version == 1  # serving during staging
    env.worker.run_once()
    doc = env.docs.get(did, tenant_id="alpha")
    assert doc.version == 2 and doc.content.strip() == CONTENT.strip()
    assert all(c.metadata["document_version"] == 2 and c.embedded for c in doc.chunks)
    assert env.search("Widget") and env.docs.source_for(("alpha", did, 2)) is not None
    assert env.docs.get_op(outcome.op.op_id).state == "done"
    assert env.tasks.get(outcome.op.task_id).status == IngestStatus.DONE
    with pytest.raises(RevisionConflict):
        reparse(env, did, key="stale", version=1)


def test_failed_parse_keeps_the_serving_version(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    did = with_source(env)
    outcome = reparse(env, did)

    def broken(data, *, filename):
        raise DocumentParseError("undecodable")

    monkeypatch.setattr(env.worker.ops.parses.parser, "parse", broken)
    env.worker.run_once()
    doc = env.docs.get(did, tenant_id="alpha")
    assert doc.version == 1 and not doc.metadata.get("lifecycle") and env.search("Widget")
    op = env.docs.get_op(outcome.op.op_id)
    assert op.state == "failed" and "undecodable" in op.error
    task = env.tasks.get(outcome.op.task_id)
    assert task.status == IngestStatus.FAILED and "undecodable" in task.message


def test_concurrent_edit_turns_reparse_into_a_publication_conflict(tmp_path):
    env = make_env(tmp_path)
    did = with_source(env)
    outcome = reparse(env, did)
    env.patch(did, 0, key="edit", text="Acme PRODUCES Gizmo.")
    env.worker.run_once()
    doc = env.docs.get(did, tenant_id="alpha")
    assert doc.version == 1 and doc.chunks[0].text == "Acme PRODUCES Gizmo."
    assert env.docs.get_op(outcome.op.op_id).state == "conflict"
    assert env.tasks.get(outcome.op.task_id).message == "publication_conflict"


def test_legacy_documents_offer_rechunk_instead_of_source_reparse(tmp_path):
    env = make_env(tmp_path)
    did = env.upload()
    with pytest.raises(ReparseUnavailable) as unavailable:
        reparse(env, did)
    assert unavailable.value.details == {"rechunk_available": True}
    with pytest.raises(InvalidCommand):
        reparse(env, did, mode="ocr")
    reparse(env, did, mode="rechunk")
    env.worker.run_once()
    assert env.docs.get(did, tenant_id="alpha").version == 2 and env.search("Gadget")


def test_async_parse_publishes_reserved_ids_and_maps_duplicates(tmp_path):
    env = make_env(tmp_path)
    existing = env.upload("# Old\n\nKnown text.\n", name="known.md")
    files = [
        StagedFile("new.md", b"# New\n\nAcme PRODUCES Widget.\n", "upload://new.md"),
        StagedFile("copy.md", b"# Old\n\nKnown text.\n", "upload://copy.md"),
    ]
    op = env.commands.stage_async_parse("alpha", files, task_id="upload-1")
    again = env.commands.stage_async_parse("alpha", files, task_id="upload-1")
    assert again.op_id == op.op_id and len(env.docs.undispatched_ops(9)) == 0
    reserved = [f["doc_id"] for f in op.payload["files"]]
    rows, _ = env.docs.list_documents(DocumentQuery("alpha", status="parsing"))
    assert {r["doc_id"] for r in rows} == set(reserved)
    env.worker.run_once()
    task = env.tasks.get("upload-1")
    assert task.status == IngestStatus.DONE
    mapping = {row["provisional_doc_id"]: row["doc_id"] for row in task.docs}
    assert mapping[reserved[0]] == reserved[0]  # new document keeps its reserved ID
    assert mapping[reserved[1]] == existing  # dedup maps provisional → canonical
    assert env.docs.get(reserved[0], tenant_id="alpha").version == 1
    assert env.docs.source_for(("alpha", reserved[0], 1)).filename == "new.md"
    assert env.docs.list_documents(DocumentQuery("alpha", status="parsing"))[1] == 0
    assert env.search("Widget")


def test_async_batch_parse_failure_accepts_nothing(tmp_path):
    env = make_env(tmp_path)
    did = env.upload("# Old\n\nKnown text.\n", name="known.md")
    files = [
        StagedFile("known.md", b"# Replacement\n\nNew text.\n", "upload://known.md"),
        StagedFile("bad.txt", b"\xff\xfe\x00", "upload://bad.txt"),
    ]
    op = env.commands.stage_async_parse("alpha", files, task_id="upload-2")
    assert op.payload["files"][0]["doc_id"] == did  # replacement reserves the stable UUID
    env.worker.run_once()
    doc = env.docs.get(did, tenant_id="alpha")
    assert doc.version == 1 and "Known" in doc.content
    task = env.tasks.get("upload-2")
    assert task.status == IngestStatus.FAILED and "bad.txt" in task.message
    errors = {row["name"]: row.get("error", "") for row in task.docs}
    assert errors["bad.txt"].startswith("parse_failed") and errors["known.md"] == ""
    failed = env.docs.document_row("alpha", op.payload["files"][1]["doc_id"])
    assert failed["status"] == "parse_failed"
