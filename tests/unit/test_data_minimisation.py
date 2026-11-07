"""Sensitive text stays out of spans, logs, audit rows and public metadata (OBS-08)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest

import agentic_graphrag.observability.logging_setup as logging_setup
from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.enums import StreamEvent
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.generation import answer
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.governance.audit_store import AuditStore
from agentic_graphrag.governance.redaction import (
    PLACEHOLDER_EMAIL,
    redact_audit_payload,
    redact_log_record,
    reset_redactor,
)
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.observability.logging_setup import JSONFormatter, setup_logging
from agentic_graphrag.observability.trace import get_tracer

EMAIL = "alice@example.com"
# Digit-heavy identifiers the phone patterns would otherwise rewrite.
DIGIT_QUERY_ID = "q-13912345678"
DIGIT_TENANT_ID = "tenant-13812345678"


@pytest.fixture
def redaction_on(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("AGR_REDACTION_ENABLED", "1")
    reset_redactor()
    yield
    reset_redactor()


@pytest.fixture
def redaction_off(monkeypatch) -> Iterator[None]:
    monkeypatch.delenv("AGR_REDACTION_ENABLED", raising=False)
    reset_redactor()
    yield
    reset_redactor()


@pytest.fixture
def clean_logging() -> Iterator[None]:
    logging_setup._CONFIGURED = False
    root = logging.getLogger(logging_setup._ROOT_LOGGER_NAME)
    saved_handlers = list(root.handlers)
    saved_propagate = root.propagate
    root.handlers.clear()
    logging_setup.clear_context()
    logging_setup._redact_hook = logging_setup._identity_redact
    yield
    logging_setup._CONFIGURED = False
    logging_setup.clear_context()
    logging_setup._redact_hook = logging_setup._identity_redact
    root.handlers[:] = saved_handlers
    root.propagate = saved_propagate


def _format(message: str) -> dict[str, object]:
    record = logging.LogRecord("x", logging.INFO, "", 0, message, (), None)
    return json.loads(JSONFormatter().format(record))


def test_setup_logging_applies_redaction_switch(redaction_on, clean_logging) -> None:
    setup_logging()
    logging_setup.bind_context(query_id=DIGIT_QUERY_ID, tenant_id=DIGIT_TENANT_ID, user_id=EMAIL)

    entry = _format(f"contact {EMAIL}")

    assert entry["message"] == f"contact {PLACEHOLDER_EMAIL}"
    assert entry["user_id"] == PLACEHOLDER_EMAIL
    # Correlation ids must survive or log joins and tenant filters break.
    assert entry["query_id"] == DIGIT_QUERY_ID
    assert entry["tenant_id"] == DIGIT_TENANT_ID


def test_setup_logging_keeps_text_when_switch_is_off(redaction_off, clean_logging) -> None:
    setup_logging()
    assert _format(f"contact {EMAIL}")["message"] == f"contact {EMAIL}"


def test_setup_logging_does_not_replace_custom_hook(redaction_on, clean_logging) -> None:
    def custom(record: dict) -> dict:
        return {**record, "message": "custom"}

    logging_setup.set_redact_hook(custom)
    setup_logging()
    assert _format("anything")["message"] == "custom"


def test_redaction_hooks_return_same_object_when_off(redaction_off) -> None:
    record = {"message": EMAIL, "query_id": DIGIT_QUERY_ID}
    assert redact_log_record(record) is record
    assert redact_audit_payload(record) is record


def _audit_row() -> dict[str, object]:
    return {
        "query_id": DIGIT_QUERY_ID,
        "question": f"Who emailed {EMAIL}?",
        "metadata": {"tenant_id": DIGIT_TENANT_ID, "note": EMAIL},
    }


def test_audit_store_redacts_when_enabled_and_keeps_lookup_keys(
    redaction_on, tmp_path: Path
) -> None:
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)

    assert store.save(_audit_row()) == DIGIT_QUERY_ID

    persisted = path.read_text(encoding="utf-8")
    assert EMAIL not in persisted
    assert PLACEHOLDER_EMAIL in persisted
    # Lookup and tenant isolation still work on the redacted row.
    row = store.get_for_tenant(DIGIT_QUERY_ID, DIGIT_TENANT_ID)
    assert row is not None
    assert PLACEHOLDER_EMAIL in row["question"]
    assert row["metadata"]["note"] == PLACEHOLDER_EMAIL
    # And a restart reads the same redacted row back.
    reloaded = AuditStore(path).get_for_tenant(DIGIT_QUERY_ID, DIGIT_TENANT_ID)
    assert reloaded == row


def test_audit_store_keeps_text_when_disabled(redaction_off, tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.jsonl")
    store.save(_audit_row())
    row = store.get(DIGIT_QUERY_ID)
    assert row is not None and EMAIL in row["question"]


def test_audit_lineage_remains_valid_while_nested_prose_is_redacted(redaction_on, tmp_path):
    from agentic_graphrag.app.audit_reads import visible_query_audit
    from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef

    digest = "a" * 53 + "13912345678"
    ref = SourceRef(
        scope=KnowledgeScope(tenant_id=DIGIT_TENANT_ID, kb_id="kb-13912345678"),
        doc_id="doc-13912345678",
        chunk_id="chunk-13912345678",
        doc_version=1,
        content_sha256=digest,
        paragraph=EMAIL,
    ).model_dump(mode="json")
    payload = {
        **_audit_row(),
        "claims": [{"text": EMAIL, "evidence_ids": ["ev_" + digest]}],
        "metadata": {
            "tenant_id": DIGIT_TENANT_ID,
            "hits": [{"content": EMAIL}],
            "evidence": [
                {
                    "id": "ev_" + digest,
                    "evidence_id": "ev_" + digest,
                    "content": EMAIL,
                    "source_refs": [ref],
                    "citations": [
                        {
                            **{
                                k: ref[k]
                                for k in ("doc_id", "chunk_id", "doc_version", "content_sha256")
                            },
                            "span": EMAIL,
                        }
                    ],
                }
            ],
            "snapshot": {"payload_sha256": digest, "document_selection_fingerprint": digest},
        },
    }
    store = AuditStore(tmp_path / "lineage.jsonl")
    store.save(payload)
    saved = AuditStore(store.path).get(DIGIT_QUERY_ID)
    evidence = saved["metadata"]["evidence"][0]
    assert SourceRef.model_validate(evidence["source_refs"][0]).content_sha256 == digest
    assert evidence["source_refs"][0] == {**ref, "paragraph": PLACEHOLDER_EMAIL}
    assert evidence["id"] == evidence["evidence_id"] == "ev_" + digest
    assert evidence["citations"][0]["doc_id"] == ref["doc_id"]
    assert saved["claims"][0]["evidence_ids"] == ["ev_" + digest]
    assert saved["metadata"]["snapshot"] == payload["metadata"]["snapshot"]
    assert EMAIL not in json.dumps(saved)
    assert payload["metadata"]["evidence"][0]["content"] == EMAIL
    from types import SimpleNamespace

    from agentic_graphrag.core.knowledge import KnowledgeBase

    svc = SimpleNamespace(knowledge_base=KnowledgeBase(**ref["scope"]), knowledge_store=None)
    assert visible_query_audit(svc, saved) == saved


@pytest.fixture
def service() -> Iterator[QueryService]:
    svc = QueryService.create_offline()
    try:
        yield svc
    finally:
        svc.close()


def _span_dump(query_id: str) -> tuple[str, list[dict]]:
    context = get_tracer().get(query_id)
    assert context is not None
    dumped = context.to_dict()
    return json.dumps(dumped, ensure_ascii=False), dumped["spans"]


def test_query_span_has_question_length_not_text(service) -> None:
    question = f"Who is the CEO of NovaTech Industries? cc {EMAIL}"

    response = service.run_query(QueryRequest(question=question), tenant_id="alpha", user_id="u")

    text, spans = _span_dump(response.query_id)
    assert EMAIL not in text and "NovaTech" not in text
    assert spans[0]["attributes"]["question_chars"] == len(question)


def test_stream_span_has_question_length_not_text(service) -> None:
    question = f"Who is the CEO of NovaTech Industries? cc {EMAIL}"
    request = QueryRequest(question=question)

    events = dict(service.stream_query_events(request, tenant_id="alpha", user_id="u"))

    text, spans = _span_dump(events[StreamEvent.ANSWER]["query_id"])
    assert EMAIL not in text and "NovaTech" not in text
    assert spans[0]["attributes"]["question_chars"] == len(question)


def test_llm_failure_exposes_exception_type_only(monkeypatch) -> None:
    secret = "https://gateway.internal/v1?api_key=sk-canary"
    monkeypatch.setattr(answer, "_llm_answer", Mock(side_effect=RuntimeError(secret)))
    evidence = Candidate(
        id="fact",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="Elena Varga is the CEO of Apex Holdings.",
        score=1.0,
    )

    chain = generate_answer(
        ReasoningChain(question="Who runs Apex Holdings?"), [evidence], MockLLMProvider()
    )

    assert chain.metadata["llm_degraded"] is True
    assert chain.metadata["llm_error"] == "RuntimeError"
    assert "sk-canary" not in json.dumps(chain.metadata, default=str)
    assert "gateway.internal" not in json.dumps(chain.metadata, default=str)
