"""Opt-in MinerU HTTP adapter (the service owns layout analysis and OCR)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from agentic_graphrag.config_knowledge import MinerUConfig
from agentic_graphrag.core.ports.parsing import (
    DocumentParseError,
    ParsedDocument,
    ParserServiceError,
    UnsupportedDocumentType,
)
from agentic_graphrag.knowledge.parsers.encoding import normalize_text
from agentic_graphrag.knowledge.parsers.sniff import MINERU_MIMES


class MinerUParser:
    def __init__(self, config: MinerUConfig) -> None:
        self.config = config

    def parse(self, content: bytes, *, filename: str) -> ParsedDocument:
        if not self.config.enabled or not self.config.endpoint.strip():
            raise UnsupportedDocumentType(
                "This file type requires MinerU; configure knowledge.parsing.mineru "
                "with enabled=true and a /file_parse endpoint, or convert to Markdown"
            )
        mime = MINERU_MIMES[Path(filename).suffix.lower()]
        response = self._request(content, filename=filename, mime=mime)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ParserServiceError("MinerU returned invalid JSON") from exc
        markdown = _markdown(payload)
        attributes = {}
        service_version = response.headers.get("X-MinerU-Version")
        if service_version:
            attributes["mineru_version"] = service_version
        return ParsedDocument(
            markdown=normalize_text(markdown).strip(),
            parser_name="mineru",
            parser_version="1",
            source_mime_type=mime,
            attributes=attributes,
        )

    def _request(self, content: bytes, *, filename: str, mime: str) -> httpx.Response:
        try:
            response = httpx.post(
                self.config.endpoint,
                files={"files": (Path(filename).name, content, mime)},
                data={
                    "return_md": "true",
                    "return_images": "false",
                    "return_content_list": "false",
                    "parse_method": "auto",
                },
                timeout=self.config.timeout_seconds,
                follow_redirects=False,
            )
            response.raise_for_status()
            return response
        except httpx.TimeoutException as exc:
            raise ParserServiceError(
                "MinerU parsing timed out; retry or increase timeout_seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise ParserServiceError("MinerU service request failed") from exc


def _markdown(payload: Any) -> str:
    """MinerU /file_parse returns one result per file under results.*.md_content."""
    if not isinstance(payload, dict):
        raise ParserServiceError("MinerU returned an invalid document response")
    results = payload.get("results")
    if isinstance(results, dict) and len(results) == 1:
        result = next(iter(results.values()))
        if isinstance(result, dict):
            markdown = result.get("md_content")
            if isinstance(markdown, str) and markdown.strip():
                return markdown
    raise DocumentParseError("MinerU returned no Markdown content for the document")
