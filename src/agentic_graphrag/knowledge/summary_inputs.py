"""Token-bounded original excerpts with exact, versioned source coordinates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.tokenizer import get_tokenizer


@dataclass(frozen=True)
class SummaryInput:
    source: SourceRef
    text: str

    @property
    def id(self) -> str:
        return hashlib.sha256(self.source.model_dump_json().encode()).hexdigest()[:24]


def document_inputs(doc: DocumentRecord, scope: KnowledgeScope) -> list[SummaryInput]:
    digest = hashlib.sha256(doc.content.encode()).hexdigest()
    return [
        SummaryInput(
            SourceRef(
                scope=scope,
                doc_id=doc.doc_id,
                doc_version=doc.version,
                chunk_id=chunk.chunk_id,
                content_sha256=digest,
                span_origin="chunk",
                span_start=0,
                span_end=len(chunk.text),
                paragraph=str(chunk.metadata.get("breadcrumb") or "") or None,
            ),
            chunk.text,
        )
        for chunk in doc.chunks
    ]


def batches(inputs: list[SummaryInput], *, token_limit: int) -> list[list[SummaryInput]]:
    codec = get_tokenizer("cl100k_base")
    pieces: list[SummaryInput] = []
    for item in inputs:
        text, offset = item.text, 0
        while text:
            prefix = codec.prefix(text, token_limit)
            if not prefix:
                raise ValueError("Summary input cannot fit the source token limit")
            ref = item.source.model_copy(
                update={
                    "span_start": item.source.span_start + offset,
                    "span_end": item.source.span_start + offset + len(prefix),
                }
            )
            pieces.append(SummaryInput(ref, prefix))
            offset += len(prefix)
            text = text[len(prefix) :]
    result: list[list[SummaryInput]] = []
    current: list[SummaryInput] = []
    used = 0
    for piece in pieces:
        size = codec.count(piece.text)
        if current and used + size > token_limit:
            result.append(current)
            current, used = [], 0
        current.append(piece)
        used += size
    return [*result, current] if current else result
