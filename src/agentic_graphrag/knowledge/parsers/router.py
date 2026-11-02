"""Dispatch validated file signatures to a single document parsing port."""

from __future__ import annotations

from dataclasses import replace

from agentic_graphrag.config_knowledge import ParsingConfig
from agentic_graphrag.core.ports.parsing import DocumentParser, ParsedDocument
from agentic_graphrag.knowledge.markdown_tables import normalize_html_tables
from agentic_graphrag.knowledge.parsers.html import HtmlParser
from agentic_graphrag.knowledge.parsers.mineru import MinerUParser
from agentic_graphrag.knowledge.parsers.sniff import sniff_mime
from agentic_graphrag.knowledge.parsers.text import TextParser


class RoutingDocumentParser:
    """Never dispatch binary files solely on their suffix or supplied MIME type."""

    def __init__(self, config: ParsingConfig) -> None:
        self.text: DocumentParser = TextParser(config)
        self.html: DocumentParser = HtmlParser(config)
        self.mineru: DocumentParser = MinerUParser(config.mineru)

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument:
        mime = sniff_mime(content, filename=filename)
        parser = self.mineru
        if mime == "text/html":
            parser = self.html
        elif mime.startswith("text/"):
            parser = self.text
        parsed = parser.parse(content, filename=filename)
        return replace(
            parsed, source_mime_type=mime, markdown=normalize_html_tables(parsed.markdown)
        )
