"""API parsing status codes and persisted parser/chunk provenance."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.config_knowledge import MinerUConfig
from agentic_graphrag.knowledge.ingest import chunk_document
from agentic_graphrag.knowledge.ingest_tasks import IngestTaskStore
from agentic_graphrag.stores.doc_store import InMemoryDocStore


@pytest.fixture()
def upload_context():
    svc = QueryService.create_offline()
    svc.cfg = svc.cfg.model_copy(deep=True)
    svc.bundle.docs = InMemoryDocStore()
    svc.ingest_tasks = IngestTaskStore()
    with TestClient(create_app(query_service=svc)) as client:
        yield client, svc
    svc.close()


def stored(response, svc):
    """The document the lifecycle stored for the first uploaded file."""
    doc_id = response.json()["data"]["docs"][0]["doc_id"]
    return svc.bundle.docs.get(doc_id, tenant_id="default")


@pytest.mark.parametrize("encoding", ["gbk", "gb18030", "utf-8-sig"])
def test_encoded_uploads_persist_normalized_text_and_metadata(upload_context, encoding) -> None:
    client, svc = upload_context
    text = "本文介绍公司年度业绩。收入增长，技术团队正在改进图谱检索系统。"
    response = client.post(
        "/v1/docs", files={"files": ("report.txt", text.encode(encoding), "text/plain")}
    )
    assert response.status_code == 200
    doc = stored(response, svc)
    assert doc is not None and doc.content == text
    # The lifecycle assigns a stable UUID; the filename is the title and source.
    uuid.UUID(doc.doc_id)
    assert doc.title == "report.txt" and doc.source_uri == "upload://report.txt"
    assert doc.metadata["parser_name"] == "text"
    assert doc.metadata["source_mime_type"] == "text/plain"
    chunk = chunk_document(doc)[0]
    assert "\ufeff" not in chunk.text and chunk.metadata["breadcrumb"] == "report.txt"


@pytest.mark.parametrize(
    "name,body,status",
    [
        ("evil.exe", b"MZ\x00payload", 415),
        ("payload", b"some text", 415),
        ("renamed.pdf", b"MZ\x00payload", 415),
        ("report.txt", bytes(range(256)), 422),
        ("broken.txt", b"\xff\xfe\x00", 422),
    ],
)
def test_upload_error_status_and_no_persistence(upload_context, name, body, status) -> None:
    client, svc = upload_context
    response = client.post("/v1/docs", files={"files": (name, body, "text/plain")})
    assert response.status_code == status
    assert svc.bundle.docs.list_ids() == []


def test_pdf_requires_explicit_mineru_configuration(upload_context) -> None:
    client, _ = upload_context
    response = client.post(
        "/v1/docs", files={"files": ("report.pdf", b"%PDF-1.7", "application/pdf")}
    )
    assert response.status_code == 415
    assert "MinerU" in response.text and "enabled=true" in response.text


def test_mineru_upload_persists_markdown_and_worker_context(upload_context, monkeypatch) -> None:
    client, svc = upload_context
    svc.cfg.knowledge.parsing.mineru = MinerUConfig(
        enabled=True, endpoint="http://mineru.test/file_parse"
    )
    markdown = "# Annual Report\n\n## Revenue\n\n| Year | Value |\n| --- | --- |\n| 2025 | 12 |"
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **k: httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"results": {"report": {"md_content": markdown}}},
        ),
    )
    response = client.post(
        "/v1/docs", files={"files": ("report.pdf", b"%PDF-1.7", "application/octet-stream")}
    )
    assert response.status_code == 200
    doc = stored(response, svc)
    assert doc is not None and doc.content == markdown
    assert doc.metadata["parser_name"] == "mineru"
    assert doc.metadata["source_mime_type"] == "application/pdf"
    worker = build_inline_worker(svc)
    worker.pipeline.graph = None  # the pipeline owns the stage; this test covers parsing
    assert worker.run_once()[0].chunks_created == 1
    hits = svc.bundle.fulltext.search("Revenue", tenant_id="default")
    assert any(chunk.metadata["heading_path"] == ["Annual Report", "Revenue"] for chunk, _ in hits)


def test_html_upload_unescapes_entities_and_strips_noise(upload_context) -> None:
    client, svc = upload_context
    html = (
        b"<html><nav>noise</nav><main><h1>Sales &amp; Research</h1>"
        b"<p>Main content.</p></main><footer>noise</footer></html>"
    )
    response = client.post(
        "/v1/docs", files={"files": ("page.html", html, "application/octet-stream")}
    )
    assert response.status_code == 200
    doc = stored(response, svc)
    assert doc is not None and "# Sales & Research" in doc.content and "noise" not in doc.content


def test_upload_byte_cap_still_returns_413(upload_context, monkeypatch) -> None:
    client, svc = upload_context
    svc.cfg.indexing.max_file_bytes = 8  # the cap is configuration, not a module constant
    response = client.post("/v1/docs", files={"files": ("too-large.txt", b"a" * 9, "text/plain")})
    assert response.status_code == 413 and svc.bundle.docs.list_ids() == []


def test_mineru_timeout_returns_service_unavailable(upload_context, monkeypatch) -> None:
    client, svc = upload_context
    svc.cfg.knowledge.parsing.mineru = MinerUConfig(
        enabled=True, endpoint="http://mineru.test/file_parse"
    )

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("internal endpoint details")

    monkeypatch.setattr(httpx, "post", timeout)
    response = client.post(
        "/v1/docs", files={"files": ("report.pdf", b"%PDF-1.7", "application/pdf")}
    )
    assert response.status_code == 503
    assert "SERVICE_UNAVAILABLE" in response.text
    assert "internal endpoint" not in response.text
    assert svc.bundle.docs.list_ids() == []


def test_oversized_code_fails_task_before_indexing(upload_context) -> None:
    client, svc = upload_context
    svc.cfg.knowledge.chunk_size_tokens = 64
    svc.cfg.knowledge.chunk_overlap_tokens = 8
    body = "# Code\n\n```python\n" + "print('example')\n" * 100 + "```"
    response = client.post("/v1/docs", files={"files": ("code.md", body.encode(), "text/markdown")})
    assert response.status_code == 200
    result = build_inline_worker(svc).run_once()[0]
    assert result.chunks_created == result.chunks_indexed == 0
    assert "increase knowledge.chunk_size_tokens" in result.error
    task = svc.ingest_tasks.get(response.json()["data"]["id"], tenant_id="default")
    assert task.status == "failed" and task.message == result.error
