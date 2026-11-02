"""MinerU is a mocked HTTP boundary in every test; OCR never runs in CI."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from agentic_graphrag.config_knowledge import MinerUConfig, ParsingConfig
from agentic_graphrag.core.ports.parsing import (
    DocumentParseError,
    DocumentParser,
    ParserServiceError,
    UnsupportedDocumentType,
)
from agentic_graphrag.knowledge.ingest import load_documents_from_dir
from agentic_graphrag.knowledge.parsers import create_parser
from agentic_graphrag.knowledge.parsers.sniff import MINERU_MIMES

MARKDOWN = (Path(__file__).parent / "fixtures/ingest/mineru.md").read_text(encoding="utf-8")
ENDPOINT = "http://mineru.test/file_parse"


def _office(extension: str) -> bytes:
    parts = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}
    out = BytesIO()
    with ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(parts[extension], "<document />")
    return out.getvalue()


SOURCES = [
    ("pdf", b"%PDF-1.7\nfixture"),
    ("docx", _office("docx")),
    ("xlsx", _office("xlsx")),
    ("pptx", _office("pptx")),
    ("png", b"\x89PNG\r\n\x1a\nfixture"),
    ("jpg", b"\xff\xd8\xfffixture"),
    ("jpeg", b"\xff\xd8\xfffixture"),
    ("tiff", b"II*\x00fixture"),
    ("tif", b"MM\x00*fixture"),
    ("bmp", b"BM\x00fixture"),
    ("webp", b"RIFF\x00\x00\x00\x00WEBPfixture"),
    ("gif", b"GIF89afixture"),
]


def _parser():
    return create_parser(
        ParsingConfig(mineru=MinerUConfig(enabled=True, endpoint=ENDPOINT, timeout_seconds=7))
    )


@pytest.mark.parametrize("extension,content", SOURCES)
def test_mineru_routes_verified_types_and_retains_markdown(monkeypatch, extension, content) -> None:
    def request(url, **kwargs):
        assert url == ENDPOINT and kwargs["timeout"] == 7
        assert kwargs["follow_redirects"] is False
        assert kwargs["data"]["parse_method"] == "auto"
        assert kwargs["files"]["files"] == (
            f"report.{extension}",
            content,
            MINERU_MIMES[f".{extension}"],
        )
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"results": {"report": {"md_content": MARKDOWN}}},
            headers={"X-MinerU-Version": "fixture-2.0"},
        )

    monkeypatch.setattr(httpx, "post", request)
    parser = _parser()
    assert isinstance(parser, DocumentParser)
    parsed = parser.parse(content, filename=f"report.{extension}")
    assert parsed.markdown == MARKDOWN.strip()
    assert parsed.parser_name == "mineru" and parsed.parser_version == "1"
    assert parsed.source_mime_type == MINERU_MIMES[f".{extension}"]
    assert parsed.attributes["mineru_version"] == "fixture-2.0"


@pytest.mark.parametrize("extension,content", SOURCES)
def test_unconfigured_mineru_is_explicitly_unsupported(extension, content) -> None:
    with pytest.raises(UnsupportedDocumentType, match="requires MinerU"):
        create_parser().parse(content, filename=f"report.{extension}")


@pytest.mark.parametrize(
    "name,content",
    [
        ("report.pdf", b"MZ\x00exe"),
        ("report.pdf", b"plain text"),
        ("report.txt", b"\x7fELF\x00exe"),
        ("report.docx", _office("xlsx")),
        ("report.png", b"%PDF-1.7\nfixture"),
        ("report.docx", b"PK\x03\x04broken"),
    ],
)
def test_mismatched_signatures_never_reach_mineru(monkeypatch, name, content) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Unverified file reached the service")

    monkeypatch.setattr(httpx, "post", forbidden)
    with pytest.raises(UnsupportedDocumentType, match="signature"):
        _parser().parse(content, filename=name)


@pytest.mark.parametrize("response", [[], {}, {"results": {"report": {"md_content": ""}}}])
def test_mineru_malformed_or_empty_results_fail(monkeypatch, response) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(200, request=httpx.Request("POST", ENDPOINT), json=response),
    )
    with pytest.raises(DocumentParseError):
        _parser().parse(b"%PDF-1.7", filename="report.pdf")


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("timeout"), httpx.ConnectError("private host detail")]
)
def test_mineru_service_errors_are_safe(monkeypatch, error) -> None:
    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(httpx, "post", failed)
    with pytest.raises(ParserServiceError, match="MinerU") as exc:
        _parser().parse(b"%PDF-1.7", filename="report.pdf")
    assert "private host" not in str(exc.value)


def test_directory_loader_passes_configured_parser(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            request=httpx.Request("POST", ENDPOINT),
            json={"results": {"report": {"md_content": MARKDOWN}}},
        ),
    )
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.7")
    docs = load_documents_from_dir(tmp_path, parser=_parser())
    assert len(docs) == 1 and docs[0].metadata["parser_name"] == "mineru"


def test_mineru_html_tables_are_persisted_as_markdown(monkeypatch) -> None:
    markdown = (
        "# Report\n\n<table><tr><th>Year</th><th>Value</th></tr>"
        "<tr><td>2025</td><td>12 &amp; growing</td></tr></table>"
    )
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **k: httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"results": {"report": {"md_content": markdown}}},
        ),
    )
    parsed = _parser().parse(b"%PDF-1.7", filename="report.pdf")
    assert "# Report" in parsed.markdown
    assert "| Year | Value |\n| --- | --- |" in parsed.markdown
    assert "| 2025 | 12 & growing |" in parsed.markdown
    assert "<table" not in parsed.markdown
