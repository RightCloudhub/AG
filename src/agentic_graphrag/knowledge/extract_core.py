"""Single-chunk triple extract core (FR-KG-01/02).

LLM structured completion + schema gate + provenance stamping.
Pipeline orchestration (journal / retry / quarantine) lives in
:mod:`agentic_graphrag.knowledge.extract_pipeline`.
"""

from __future__ import annotations

from agentic_graphrag.config import load_prompt
from agentic_graphrag.knowledge.schema_check import (
    ExtractResult,
    SchemaDefinition,
    Triple,
    gate_triples,
)
from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.stores.interfaces import ChunkRecord


def extract_from_chunk(
    chunk: ChunkRecord,
    schema: SchemaDefinition,
    llm: LLMProvider,
    *,
    confidence_threshold: float = 0.5,
) -> tuple[list[Triple], list[tuple[Triple, str]]]:
    """Single-chunk LLM extract + gate (backward-compatible)."""
    result = llm_extract_raw(chunk, schema, llm)
    for t in result.triples:
        t.source_doc_id = t.source_doc_id or chunk.doc_id
        t.source_chunk_id = t.source_chunk_id or chunk.chunk_id
        stamp_provenance(t, chunk)
    gated = gate_triples(result.triples, schema, confidence_threshold=confidence_threshold)
    return gated.accepted, gated.rejected


def extract_from_chunks(
    chunks: list[ChunkRecord],
    schema: SchemaDefinition,
    llm: LLMProvider,
    *,
    confidence_threshold: float = 0.5,
) -> tuple[list[Triple], list[tuple[Triple, str]]]:
    """Simple sequential extract (no journal). Prefer pipeline runner."""
    all_accepted: list[Triple] = []
    all_rejected: list[tuple[Triple, str]] = []
    for chunk in chunks:
        accepted, rejected = extract_from_chunk(
            chunk, schema, llm, confidence_threshold=confidence_threshold
        )
        all_accepted.extend(accepted)
        all_rejected.extend(rejected)
    return all_accepted, all_rejected


def llm_extract_raw(
    chunk: ChunkRecord, schema: SchemaDefinition, llm: LLMProvider
) -> ExtractResult:
    prompt_template = load_prompt("extract")
    system, user = split_prompt(
        prompt_template.format(
            schema_summary=schema.summary(),
            doc_id=chunk.doc_id,
            chunk_id=chunk.chunk_id,
            chunk_text=chunk.text,
        )
    )
    messages = [
        Message(role="system", content=system),
        Message(role="user", content=user),
    ]
    return complete_structured(llm, messages, ExtractResult, tier=Tier.STRONG)


def stamp_provenance(triple: Triple, chunk: ChunkRecord, *, batch_id: str | None = None) -> None:
    """Attach source metadata for FR-KG-01/02 audit trail."""
    attrs = dict(triple.attributes or {})
    attrs.setdefault("source_doc_id", chunk.doc_id)
    attrs.setdefault("source_chunk_id", chunk.chunk_id)
    if batch_id:
        attrs["extract_batch_id"] = batch_id
    title = (chunk.metadata or {}).get("title")
    if title:
        attrs.setdefault("source_title", title)
    path = (chunk.metadata or {}).get("source_path") or (chunk.metadata or {}).get("filename")
    if path:
        attrs.setdefault("source_path", path)
    triple.attributes = attrs


def split_prompt(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        system = parts[0].replace("# System", "", 1).strip()
        user = parts[1].strip()
        return system, user
    return "You are a knowledge extraction engine.", text
