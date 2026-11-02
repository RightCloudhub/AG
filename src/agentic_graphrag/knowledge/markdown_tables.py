"""Normalize embedded HTML tables while leaving fenced examples verbatim."""

from __future__ import annotations

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from agentic_graphrag.knowledge.parsers.html_markdown import render_html


def normalize_html_tables(markdown: str) -> str:
    if "<table" not in markdown.lower():
        return markdown
    lines = markdown.splitlines(keepends=True)
    tokens = MarkdownIt("commonmark").parse(markdown)
    for token in reversed(tokens):
        if token.type == "html_block" and token.level == 0 and token.map:
            start, end = token.map
            replacement = _convert_table_block("".join(lines[start:end]))
            if replacement is not None:
                lines[start:end] = [replacement + "\n\n"]
    return "".join(lines)


def _convert_table_block(source: str) -> str | None:
    soup = BeautifulSoup(source, "html.parser")
    tables = [table for table in soup.find_all("table") if not table.find_parent(["pre", "code"])]
    return render_html(soup).strip() if tables else None
