"""Markdown/plain-text adapter with explicit encoding provenance."""

from __future__ import annotations

from pathlib import Path

from agentic_graphrag.config_knowledge import ParsingConfig
from agentic_graphrag.core.ports.parsing import ParsedDocument
from agentic_graphrag.knowledge.parsers.encoding import decode_text
from agentic_graphrag.knowledge.parsers.sniff import TEXT_MIMES


class TextParser:
    def __init__(self, config: ParsingConfig | None = None) -> None:
        self.config = config or ParsingConfig()

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument:
        decoded = decode_text(content, self.config)
        return ParsedDocument(
            markdown=decoded.text,
            parser_name="text",
            parser_version="1",
            source_mime_type=TEXT_MIMES.get(Path(filename).suffix.lower(), "text/plain"),
            attributes={"source_encoding": decoded.encoding},
        )
