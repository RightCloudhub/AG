"""P2-KG-01: journal, retry, resume, quarantine, provenance (no live LLM)."""

from pathlib import Path

from agentic_graphrag.config import resolve_path
from agentic_graphrag.knowledge.extraction import (
    ExtractStatus,
    RetryPolicy,
    load_completed_chunk_ids,
    run_extract_pipeline,
)
from agentic_graphrag.knowledge.schema_check import (
    EntityMention,
    ExtractResult,
    Triple,
    load_schema,
)
from agentic_graphrag.stores.doc_store import InMemoryDocStore
from agentic_graphrag.stores.interfaces import ChunkRecord, DocumentRecord


def _schema():
    return load_schema(resolve_path("configs/schema/domain_v0.yaml"))


def _chunk(cid: str = "d1:0", doc: str = "d1", text: str = "Elena is CEO of Apex.") -> ChunkRecord:
    return ChunkRecord(
        chunk_id=cid,
        doc_id=doc,
        text=text,
        index=0,
        metadata={"title": "Bio", "source_path": "data/raw/bio.md"},
    )


def test_retry_policy_logic():
    p = RetryPolicy(max_attempts=3, base_delay_seconds=1.0)
    assert p.should_retry(1, RuntimeError("x"))
    assert p.should_retry(2, RuntimeError("x"))
    assert not p.should_retry(3, RuntimeError("x"))
    assert p.delay_before_attempt(1) == 0.0
    assert p.delay_before_attempt(2) == 1.0
    assert p.delay_before_attempt(3) == 2.0


def test_pipeline_success_and_provenance(tmp_path: Path):
    schema = _schema()

    def extract_fn(chunk, _schema):
        return ExtractResult(
            triples=[
                Triple(
                    head=EntityMention(name="Elena Varga", type="Person"),
                    relation="CEO_OF",
                    tail=EntityMention(name="Apex Holdings", type="Company"),
                    confidence=0.95,
                )
            ]
        )

    journal = tmp_path / "journal.jsonl"
    quarantine = tmp_path / "quarantine.jsonl"
    docs = InMemoryDocStore()
    docs.save(DocumentRecord(doc_id="d1", title="Bio", content="..."))

    result = run_extract_pipeline(
        [_chunk()],
        schema,
        extract_fn=extract_fn,
        confidence_threshold=0.5,
        retry=RetryPolicy(max_attempts=2, base_delay_seconds=0),
        journal_path=journal,
        quarantine_path=quarantine,
        doc_store=docs,
        sleep_fn=lambda _d: None,
        batch_id="testbatch",
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].attributes.get("extract_batch_id") == "testbatch"
    assert result.accepted[0].source_chunk_id == "d1:0"
    assert result.failed_count == 0
    assert journal.exists()
    completed = load_completed_chunk_ids(journal)
    assert "d1:0" in completed
    doc = docs.get("d1")
    assert doc is not None
    assert doc.metadata.get("last_extract_batch_id") == "testbatch"


def test_pipeline_resume_skips_completed(tmp_path: Path):
    schema = _schema()
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        '{"chunk_id":"d1:0","doc_id":"d1","status":"ok","attempts":1}\n',
        encoding="utf-8",
    )
    calls = {"n": 0}

    def extract_fn(chunk, _schema):
        calls["n"] += 1
        return ExtractResult(triples=[])

    result = run_extract_pipeline(
        [_chunk("d1:0"), _chunk("d1:1")],
        schema,
        extract_fn=extract_fn,
        journal_path=journal,
        retry=RetryPolicy(max_attempts=1, base_delay_seconds=0),
        sleep_fn=lambda _d: None,
    )
    assert result.skipped_count == 1
    assert calls["n"] == 1  # only second chunk


def test_pipeline_quarantine_after_retries(tmp_path: Path):
    schema = _schema()
    attempts = {"n": 0}

    def extract_fn(chunk, _schema):
        attempts["n"] += 1
        raise RuntimeError("llm down")

    quarantine = tmp_path / "q.jsonl"
    result = run_extract_pipeline(
        [_chunk()],
        schema,
        extract_fn=extract_fn,
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0),
        quarantine_path=quarantine,
        sleep_fn=lambda _d: None,
    )
    assert result.failed_count == 1
    assert attempts["n"] == 3
    assert quarantine.exists()
    assert "llm down" in quarantine.read_text(encoding="utf-8")
    assert result.chunk_results[0].status == ExtractStatus.FAILED
