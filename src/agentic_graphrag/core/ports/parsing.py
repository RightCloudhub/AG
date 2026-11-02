"""File-to-Markdown boundary shared by upload and command-line ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class DocumentParseError(ValueError):
    """The document could not be converted to usable Markdown."""


class UnsupportedDocumentType(DocumentParseError):
    """Unknown/mismatched file signature, or a required parser is disabled."""


class UndecodableDocument(DocumentParseError):
    """No sufficiently reliable text encoding could be detected."""


class ParserServiceError(DocumentParseError):
    """An explicitly configured parsing service failed."""


@dataclass(frozen=True)
class ParsedDocument:
    markdown: str
    parser_name: str
    parser_version: str
    source_mime_type: str
    attributes: dict[str, str] = field(default_factory=dict)

    def metadata(self) -> dict[str, str]:
        return {
            **self.attributes,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "source_mime_type": self.source_mime_type,
        }


@runtime_checkable
class DocumentParser(Protocol):
    """Adapters return Unicode Markdown, without changing source identity."""

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument: ...
