"""Offline regressions for the shared document parsing boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_graphrag.knowledge.ingest import chunk_document, load_document

CHINESE = "本文介绍公司年度业绩。收入增长，技术团队正在改进图谱检索系统。"
HTML = """<!doctype html><html><body>
<nav>Navigation noise</nav><main><h1>Research &amp; Development</h1>
<p>The research team builds reliable systems &amp; publishes detailed annual results.</p>
<h2>Results</h2><table><tr><th>Year</th><th>Revenue</th></tr>
<tr><td>2025</td><td>10 &amp; growing</td></tr></table></main>
<footer>Footer noise</footer><script>script_noise()</script></body></html>"""


@pytest.mark.parametrize("encoding", ["gbk", "gb18030", "utf-8-sig"])
def test_text_encoding_is_normalized(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "report.txt"
    path.write_bytes(CHINESE.encode(encoding))
    doc = load_document(path)
    assert doc.content == CHINESE
    assert "\ufeff" not in chunk_document(doc)[0].text


def test_html_extracts_structure_and_unescapes_entities(tmp_path: Path) -> None:
    path = tmp_path / "report.html"
    path.write_text(HTML, encoding="utf-8")
    doc = load_document(path)
    assert "# Research & Development" in doc.content
    assert "## Results" in doc.content
    assert "| Year | Revenue |" in doc.content
    assert "10 & growing" in doc.content
    assert "noise" not in doc.content
    assert "&amp;" not in doc.content


def test_disguised_executable_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "renamed.pdf"
    path.write_bytes(b"MZ\x90\x00executable payload")
    with pytest.raises(ValueError, match="signature|type"):
        load_document(path)


def test_parser_metadata_and_cli_document_id_are_preserved(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "article.md"
    path.write_text("# Heading\n\nBody", encoding="utf-8")
    doc = load_document(path)
    assert doc.doc_id == hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
    assert doc.metadata["parser_name"] == "text"
    assert doc.metadata["parser_version"] == "1"
    assert doc.metadata["source_mime_type"] == "text/markdown"
    assert doc.metadata["source_encoding"] == "utf-8"
    assert chunk_document(doc)[0].metadata["parser_name"] == "text"


@pytest.mark.parametrize("encoding", ["utf-16", "gb18030", "utf-8-sig"])
def test_html_encoding_and_mislabeled_text_route(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "article.txt"
    path.write_bytes(
        ("<html><main><h1>标题</h1><p>" + CHINESE + "</p></main></html>").encode(encoding)
    )
    doc = load_document(path)
    assert CHINESE in doc.content
    assert doc.metadata["source_mime_type"] == "text/html"
    assert doc.metadata["parser_name"] == "html"


def test_short_gbk_text_uses_configured_tiebreaker(tmp_path: Path) -> None:
    path = tmp_path / "short.txt"
    path.write_bytes("你好世界".encode("gbk"))
    assert load_document(path).content == "你好世界"


def test_html_fallback_retains_structure_on_extraction_failure(monkeypatch) -> None:
    from agentic_graphrag.knowledge.parsers.html import HtmlParser

    def failed_extract(*args, **kwargs):
        raise ValueError("No extracted article")

    monkeypatch.setattr("trafilatura.extract", failed_extract)
    parsed = HtmlParser().parse(HTML.encode(), filename="article.html")
    assert parsed.attributes["extractor"] == "beautifulsoup4"
    assert "# Research & Development" in parsed.markdown
    assert "| Year | Revenue |" in parsed.markdown


def test_html_falls_back_when_extractor_loses_structure(monkeypatch) -> None:
    from agentic_graphrag.knowledge.parsers.html import HtmlParser

    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: "Unstructured output")
    parsed = HtmlParser().parse(HTML.encode(), filename="article.html")
    assert parsed.attributes["extractor"] == "beautifulsoup4"
    assert "# Research & Development" in parsed.markdown


def test_trafilatura_main_content_is_used_when_structured(monkeypatch) -> None:
    from agentic_graphrag.knowledge.parsers.html import HtmlParser

    markdown = "# Main article\n\nBody extracted by trafilatura."
    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: markdown)
    parsed = HtmlParser().parse(b"<h1>Main article</h1><p>Body</p>", filename="article.html")
    assert parsed.markdown == markdown
    assert parsed.attributes["extractor"] == "trafilatura"


def test_markdown_html_code_does_not_route_to_html(tmp_path: Path) -> None:
    path = tmp_path / "html-example.md"
    body = "# HTML example\n\n```html\n<html><p>Code example</p></html>\n```"
    path.write_text(body, encoding="utf-8")
    doc = load_document(path)
    assert doc.content == body
    assert doc.metadata["parser_name"] == "text"


@pytest.mark.parametrize("body", [b"PK is a company.", b"plain text\n\tmore text", b""])
def test_plain_text_and_empty_files_are_accepted(tmp_path: Path, body: bytes) -> None:
    path = tmp_path / "plain.txt"
    path.write_bytes(body)
    assert load_document(path).content == body.decode()


def test_html_with_only_noise_is_rejected() -> None:
    from agentic_graphrag.core.ports.parsing import DocumentParseError
    from agentic_graphrag.knowledge.parsers import create_parser

    with pytest.raises(DocumentParseError, match="no extractable"):
        create_parser().parse(b"<html><nav>Noise</nav></html>", filename="empty.html")


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<ul><li>First item</li><li>Second item</li></ul>", "- First item"),
        ("<pre>print('example')\n# comment</pre>", "```\nprint('example')\n# comment\n```"),
    ],
)
def test_html_fragments_preserve_lists_and_code(monkeypatch, html, expected) -> None:
    from agentic_graphrag.knowledge.parsers import create_parser

    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: None)
    parsed = create_parser().parse(html.encode(), filename="fragment.html")
    assert expected in parsed.markdown
