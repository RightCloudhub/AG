"""Parser adapters and the single signature-checked ingestion entry point."""

from __future__ import annotations

from agentic_graphrag.config_knowledge import ParsingConfig
from agentic_graphrag.core.ports.parsing import DocumentParser


def create_parser(config: ParsingConfig | None = None) -> DocumentParser:
    from agentic_graphrag.knowledge.parsers.router import RoutingDocumentParser

    if config is None:
        from agentic_graphrag.config import get_config

        config = get_config().knowledge.parsing
    return RoutingDocumentParser(config)
