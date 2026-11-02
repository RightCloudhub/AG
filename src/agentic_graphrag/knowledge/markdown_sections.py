"""Read CommonMark/GFM source maps so chunk boundaries respect block structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

from agentic_graphrag.knowledge.markdown_tables import normalize_html_tables

BlockKind = Literal["prose", "table", "code", "list", "quote"]


@dataclass(frozen=True)
class MarkdownBlock:
    text: str
    kind: BlockKind = "prose"


@dataclass
class MarkdownSection:
    heading_path: tuple[str, ...]
    blocks: list[MarkdownBlock] = field(default_factory=list)


def markdown_sections(text: str) -> list[MarkdownSection]:
    text = normalize_html_tables(text)
    lines = text.splitlines(keepends=True)
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    sections: list[MarkdownSection] = []
    headings: list[tuple[int, str]] = []
    current = MarkdownSection(())
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0:
            if current.blocks:
                sections.append(current)
            headings = _next_headings(headings, token, tokens[index + 1])
            current = MarkdownSection(tuple(title for _, title in headings))
        elif _is_block(token):
            assert token.map is not None
            start, end = token.map
            source = "".join(lines[start:end]).rstrip("\r\n")
            current.blocks.append(MarkdownBlock(source, _block_kind(token)))
    if current.blocks or current.heading_path:
        sections.append(current)
    return sections


def _next_headings(
    headings: list[tuple[int, str]], token: Token, inline: Token
) -> list[tuple[int, str]]:
    depth = int(token.tag[1:])
    return [(level, title) for level, title in headings if level < depth] + [
        (depth, _heading_text(inline))
    ]


def _is_block(token: Token) -> bool:
    if token.map is None:
        return False
    if token.type == "list_item_open":
        return token.level == 1
    return token.level == 0 and token.type not in {"bullet_list_open", "ordered_list_open"}


def _block_kind(token: Token) -> BlockKind:
    if token.type == "table_open":
        return "table"
    if token.type in {"fence", "code_block"}:
        return "code"
    if token.type == "list_item_open":
        return "list"
    if token.type == "blockquote_open":
        return "quote"
    return "prose"


def _heading_text(token: Token) -> str:
    children = token.children or []
    title = "".join(
        child.content for child in children if child.type in {"text", "code_inline", "image"}
    )
    return " ".join((title or token.content).split())
