"""Main-content HTML extraction with a structure-preserving local fallback."""

from __future__ import annotations

from importlib.metadata import version

import trafilatura
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from agentic_graphrag.config_knowledge import ParsingConfig
from agentic_graphrag.core.ports.parsing import DocumentParseError, ParsedDocument
from agentic_graphrag.knowledge.parsers.encoding import decode_text, normalize_text
from agentic_graphrag.knowledge.parsers.html_markdown import render_html

_NOISE = "script, style, nav, footer, aside, form, noscript, [role=navigation], [role=contentinfo]"


class HtmlParser:
    def __init__(self, config: ParsingConfig | None = None) -> None:
        self.config = config or ParsingConfig()

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument:
        decoded = decode_text(content, self.config)
        soup = BeautifulSoup(decoded.text, "html.parser")
        for noise in soup.select(_NOISE):
            noise.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        markdown = _extract(str(main))
        extractor = "trafilatura"
        if not markdown or _lost_structure(str(main), markdown):
            markdown = render_html(main).strip()
            extractor = "beautifulsoup4"
        if not markdown:
            raise DocumentParseError("HTML contains no extractable main content")
        return ParsedDocument(
            markdown=normalize_text(markdown).strip(),
            parser_name="html",
            parser_version="1",
            source_mime_type="text/html",
            attributes={
                "source_encoding": decoded.encoding,
                "extractor": extractor,
                "extractor_version": version(extractor),
            },
        )


def _extract(html: str) -> str | None:
    try:
        return trafilatura.extract(
            html,
            output_format="markdown",
            include_tables=True,
            include_formatting=True,
            include_links=True,
            include_comments=False,
            fast=True,
        )
    except (ValueError, TypeError):
        return None


def _lost_structure(html: str, markdown: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    tokens = MarkdownIt("commonmark").enable("table").parse(markdown)
    headings = sum(token.type == "heading_open" for token in tokens)
    tables = sum(token.type == "table_open" for token in tokens)
    return headings < len(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])) or tables < len(
        soup.find_all("table")
    )
