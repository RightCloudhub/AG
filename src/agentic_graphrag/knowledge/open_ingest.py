"""Open extraction is capability-gated and never invokes domain rules or Schema gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import MessageRole, PromptName
from agentic_graphrag.core.events import GraphCommitted
from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.entities import EntityEmbedder, EntitySemanticIndex
from agentic_graphrag.core.ports.llm import CompletionClient, Message
from agentic_graphrag.core.ports.stores import ChunkRecord, RelationRecord
from agentic_graphrag.knowledge.open_builder import relation_record, stage_resolution, write_records
from agentic_graphrag.knowledge.open_contracts import OpenExtraction
from agentic_graphrag.knowledge.open_resolution import OpenResolver, ResolvedEntity
from agentic_graphrag.knowledge.open_sources import source_ref
from agentic_graphrag.llm.roles import for_role
from agentic_graphrag.llm.structured import complete_structured

if TYPE_CHECKING:
    from agentic_graphrag.knowledge.graph_ingest import GraphIngestor, GraphIngestResult


@dataclass
class OpenRuntime:
    knowledge_base: KnowledgeBase
    index: EntitySemanticIndex | None = None
    space: str = ""
    embedder: EntityEmbedder | None = None
    judge: CompletionClient | None = None


def ingest_open(
    ingestor: GraphIngestor, chunks: list[ChunkRecord], *, runtime: OpenRuntime, batch_id: str
) -> GraphIngestResult:
    from agentic_graphrag.knowledge.graph_ingest import GraphIngestResult

    result = GraphIngestResult(extractor="open_llm")
    llm, index, embedder = ingestor.llm, runtime.index, runtime.embedder
    if llm is None or index is None or embedder is None or not runtime.space:
        result.error = "open_capability_unavailable: extractor, embedding and entity index required"
        return result
    resolver = OpenResolver(
        graph=ingestor.graph,
        index=index,
        embedder=embedder,
        space=runtime.space,
        judge=runtime.judge,
    )
    for chunk in chunks:
        _ingest_chunk(
            ingestor, chunk, runtime=runtime, resolver=resolver, result=result, batch_id=batch_id
        )
    if ingestor.events is not None:
        ingestor.events.publish(
            GraphCommitted(tenant_id=runtime.knowledge_base.tenant_id, source="open_ingest")
        )
    if ingestor.on_commit is not None:
        ingestor.on_commit()
    return result


def _ingest_chunk(
    ingestor: GraphIngestor,
    chunk: ChunkRecord,
    *,
    runtime: OpenRuntime,
    resolver: OpenResolver,
    result: GraphIngestResult,
    batch_id: str,
) -> None:
    assert ingestor.llm is not None
    scope = runtime.knowledge_base.scope
    if (chunk.tenant_id or "default") != scope.tenant_id or chunk.kb_id != scope.kb_id:
        raise ValueError("Extraction chunk belongs to another knowledge scope")
    messages = [
        Message(role=MessageRole.SYSTEM, content=load_prompt(PromptName.OPEN_EXTRACT)),
        Message(role=MessageRole.USER, content=chunk.text),
    ]
    extracted = complete_structured(
        for_role(ingestor.llm, ModelRole.EXTRACTOR), messages, OpenExtraction, max_retries=1
    )
    resolved, relations = _build_records(extracted, chunk, resolver=resolver, scope=scope)
    result.conflicts_review += stage_resolution(resolved, ingestor.review_queue, batch_id=batch_id)
    write_records(ingestor.graph, resolved, relations, index=resolver.index, space=resolver.space)
    result.triples_extracted += len(extracted.relations)
    result.accepted += len(relations)
    result.entities_accepted += len(resolved)
    result.rejected += len(extracted.relations) - len(relations)


def _build_records(
    extracted: OpenExtraction, chunk: ChunkRecord, *, resolver: OpenResolver, scope: KnowledgeScope
) -> tuple[list[ResolvedEntity], list[RelationRecord]]:
    from agentic_graphrag.knowledge.schema_check import default_confidence_threshold

    refs = [source_ref(chunk, entity.evidence, scope=scope) for entity in extracted.entities]
    rel_refs = [source_ref(chunk, rel.evidence, scope=scope) for rel in extracted.relations]
    vectors = (
        resolver.embedder.embed_many([entity.description for entity in extracted.entities])
        if refs
        else []
    )
    if len(vectors) != len(refs):
        raise ValueError("Entity embedding returned the wrong number of vectors")
    resolved = [
        resolver.resolve(item, ref, vector=vector)
        for item, ref, vector in zip(extracted.entities, refs, vectors, strict=True)
    ]
    by_ref = {entity.local_ref: entity for entity in resolved}
    relations = [
        relation_record(rel, ref, entities=by_ref)
        for rel, ref in zip(extracted.relations, rel_refs, strict=True)
        if rel.confidence >= default_confidence_threshold()
    ]
    return resolved, relations
