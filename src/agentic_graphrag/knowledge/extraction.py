"""LLM triple extraction (FR-KG-02)."""

from __future__ import annotations

from agentic_graphrag.config import load_prompt
from agentic_graphrag.knowledge.schema_check import (
    ExtractResult,
    SchemaDefinition,
    Triple,
    validate_triples,
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
    prompt_template = load_prompt("extract")
    # Split system / user sections if present
    system, user = _split_prompt(
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
    result = complete_structured(llm, messages, ExtractResult, tier=Tier.STRONG)
    for t in result.triples:
        t.source_doc_id = t.source_doc_id or chunk.doc_id
        t.source_chunk_id = t.source_chunk_id or chunk.chunk_id

    validated = validate_triples(result.triples, schema)
    accepted = [t for t in validated.accepted if t.confidence >= confidence_threshold]
    rejected = list(validated.rejected)
    for t in validated.accepted:
        if t.confidence < confidence_threshold:
            rejected.append((t, f"confidence {t.confidence} < {confidence_threshold}"))
    return accepted, rejected


def extract_from_chunks(
    chunks: list[ChunkRecord],
    schema: SchemaDefinition,
    llm: LLMProvider,
    *,
    confidence_threshold: float = 0.5,
) -> tuple[list[Triple], list[tuple[Triple, str]]]:
    all_accepted: list[Triple] = []
    all_rejected: list[tuple[Triple, str]] = []
    for chunk in chunks:
        accepted, rejected = extract_from_chunk(
            chunk, schema, llm, confidence_threshold=confidence_threshold
        )
        all_accepted.extend(accepted)
        all_rejected.extend(rejected)
    return all_accepted, all_rejected


def _split_prompt(text: str) -> tuple[str, str]:
    if "# System" in text and "# User" in text:
        parts = text.split("# User", 1)
        system = parts[0].replace("# System", "", 1).strip()
        user = parts[1].strip()
        return system, user
    return "You are a knowledge extraction engine.", text
