"""Recursive prose splitting and row-preserving table subdivision."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentic_graphrag.knowledge.markdown_sections import MarkdownBlock
from agentic_graphrag.knowledge.tokenizer import TokenCodec

PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
SENTENCE_BREAK = re.compile(r"(?<=[。！？；])|(?<=[.!?;])(?=\s|$)")
_SEPARATORS = (PARAGRAPH_BREAK, SENTENCE_BREAK)
BLOCK_SEPARATOR = "\n\n"


class ChunkingError(ValueError):
    """An indivisible structure cannot fit the configured token budget."""


@dataclass(frozen=True)
class ChunkBudget:
    codec: TokenCodec
    limit: int
    overlap: int
    breadcrumb: str

    @property
    def prefix(self) -> str:
        return self.breadcrumb + BLOCK_SEPARATOR

    def fits(self, body: str) -> bool:
        return self.codec.count(self.prefix + body) <= self.limit

    def prose_limit(self) -> int:
        capacity = self.limit - self.codec.count(self.prefix)
        # Reserve space for overlap when falling back to token-level splits.
        carry = min(self.overlap, max(0, capacity // 2))
        return max(1, capacity - carry - self.codec.count(BLOCK_SEPARATOR))


def split_block(block: MarkdownBlock, budget: ChunkBudget) -> list[MarkdownBlock]:
    if budget.fits(block.text):
        return [block]
    if block.kind == "table":
        return _split_table(block.text, budget)
    if block.kind in {"code", "list", "quote"}:
        raise ChunkingError(
            f"A {block.kind} block exceeds {budget.limit} tokens including breadcrumb; "
            "increase knowledge.chunk_size_tokens"
        )
    pieces = _split_prose(block.text, budget.codec, limit=budget.prose_limit())
    if any(not budget.fits(piece) for piece in pieces):
        raise ChunkingError("Breadcrumb leaves insufficient token space for document text")
    return [MarkdownBlock(piece) for piece in pieces]


def _split_prose(text: str, codec: TokenCodec, *, limit: int, level: int = 0) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if codec.count(text) <= limit:
        return [text]
    if level == len(_SEPARATORS):
        return _token_pieces(text, codec, limit=limit)
    result = []
    for piece in _SEPARATORS[level].split(text):
        result.extend(_split_prose(piece, codec, limit=limit, level=level + 1))
    return result


def _token_pieces(text: str, codec: TokenCodec, *, limit: int) -> list[str]:
    result = []
    while text:
        piece = codec.prefix(text, limit)
        if not piece:
            raise ChunkingError(
                "Token limit cannot fit a Unicode character; increase chunk_size_tokens"
            )
        result.append(piece)
        text = text[len(piece) :]
    return result


def _split_table(text: str, budget: ChunkBudget) -> list[MarkdownBlock]:
    lines = text.splitlines()
    header = "\n".join(lines[:2])
    pieces: list[MarkdownBlock] = []
    rows: list[str] = []
    for row in lines[2:]:
        if not budget.fits(header + "\n" + row):
            raise ChunkingError(
                "Table header and a single row exceed the token limit; increase chunk_size_tokens"
            )
        candidate = "\n".join([header, *rows, row])
        if rows and not budget.fits(candidate):
            pieces.append(MarkdownBlock("\n".join([header, *rows]), "table"))
            rows = []
        rows.append(row)
    if rows:
        pieces.append(MarkdownBlock("\n".join([header, *rows]), "table"))
    if not pieces:
        raise ChunkingError("Table header exceeds the token limit; increase chunk_size_tokens")
    return pieces
