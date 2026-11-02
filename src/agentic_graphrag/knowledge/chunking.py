"""Heading-aware Markdown chunks measured in tokens, including their breadcrumb."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.config_knowledge import (
    DEFAULT_CHUNK_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_TOKENIZER,
)
from agentic_graphrag.knowledge.chunk_splitting import (
    BLOCK_SEPARATOR,
    ChunkBudget,
    ChunkingError,
    split_block,
)
from agentic_graphrag.knowledge.markdown_sections import (
    MarkdownBlock,
    MarkdownSection,
    markdown_sections,
)
from agentic_graphrag.knowledge.tokenizer import get_tokenizer


@dataclass(frozen=True)
class MarkdownChunk:
    text: str
    breadcrumb: str
    heading_path: tuple[str, ...]
    token_count: int
    tokenizer: str


def chunk_markdown(
    text: str,
    *,
    title: str = "Document",
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_OVERLAP_TOKENS,
    tokenizer: str = DEFAULT_TOKENIZER,
) -> list[MarkdownChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    codec = get_tokenizer(tokenizer)
    title = " ".join(title.split()) or "Document"
    result = []
    for section in markdown_sections(text.lstrip("\ufeff")):
        breadcrumb = " > ".join((title, *section.heading_path))
        budget = ChunkBudget(codec, chunk_size, max(0, min(overlap, chunk_size - 1)), breadcrumb)
        if not budget.fits(""):
            raise ChunkingError("Breadcrumb exceeds the token limit; increase chunk_size_tokens")
        for body in _pack_section(section, budget):
            rendered = budget.prefix + body if body else breadcrumb
            result.append(
                MarkdownChunk(
                    rendered, breadcrumb, section.heading_path, codec.count(rendered), codec.name
                )
            )
    return result


def _pack_section(section: MarkdownSection, budget: ChunkBudget) -> list[str]:
    pieces = [piece for block in section.blocks for piece in split_block(block, budget)]
    result = []
    current: list[MarkdownBlock] = []
    for piece in pieces:
        candidate = _body([*current, piece])
        if current and not budget.fits(candidate):
            result.append(_body(current))
            carry = _overlap(current, piece, budget)
            current = [MarkdownBlock(carry)] if carry else []
        current.append(piece)
    if current:
        result.append(_body(current))
    return result or [""]


def _body(blocks: list[MarkdownBlock]) -> str:
    return BLOCK_SEPARATOR.join(block.text for block in blocks)


def _overlap(blocks: list[MarkdownBlock], incoming: MarkdownBlock, budget: ChunkBudget) -> str:
    if incoming.kind != "prose":
        return ""
    tail: list[str] = []
    for block in reversed(blocks):
        if block.kind != "prose":
            break
        tail.insert(0, block.text)
    previous = BLOCK_SEPARATOR.join(tail)
    limit = min(
        budget.overlap,
        max(0, budget.limit - budget.codec.count(budget.prefix + incoming.text + BLOCK_SEPARATOR)),
    )
    carry = budget.codec.suffix(previous, limit).strip()
    while carry and not budget.fits(carry + BLOCK_SEPARATOR + incoming.text):
        limit -= 1
        carry = budget.codec.suffix(previous, limit).strip()
    return carry
