"""Runtime document → knowledge-graph stage (docs/design/BUSINESS_LOGIC.md BL-01).

Chunks of an uploaded document go through the same write path the CLI uses:

    extract (LLM, or the deterministic rule extractor offline)
      → schema + confidence gate
      → entity resolution against names already in the graph
      → incremental update (conflicts by cardinality / time / confidence)
      → uncertain conflicts → ``ReviewQueue`` (decided by the review executor)

The ingest worker calls :meth:`GraphIngestor.ingest_chunks` after indexing a
document, so an upload reaches the graph without a separate CLI step.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from agentic_graphrag.core.events import EventBus
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.capabilities import EntityLister
from agentic_graphrag.core.ports.entities import EntityEmbedder, EntitySemanticIndex
from agentic_graphrag.core.ports.llm import CompletionClient, LLMClient
from agentic_graphrag.core.ports.stores import ChunkRecord, GraphStore
from agentic_graphrag.knowledge.extract_rules import extract_rules_from_chunk
from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.resolution import normalize_key
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.knowledge.schema_check import SchemaDefinition, Triple, default_schema
from agentic_graphrag.observability.degradation import REASON_EXTRACT_FAILED, record_degradation

_ENTITY_SCAN_LIMIT = 100_000


@dataclass
class GraphIngestResult:
    """What one document contributed to the graph."""

    triples_extracted: int = 0
    entities_accepted: int = 0
    accepted: int = 0
    rejected: int = 0
    conflicts_auto: int = 0
    conflicts_review: int = 0
    conflicts_temporal: int = 0
    conflicts_kept: int = 0
    extractor: str = "rules"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GraphIngestDeps:
    """Collaborators of the graph stage; only ``graph`` is required."""

    graph: GraphStore
    review_queue: ReviewQueue | None = None
    llm: LLMClient | None = None
    schema: SchemaDefinition | None = None
    on_commit: Callable[[], None] | None = None  # legacy hook; prefer ``events``
    events: EventBus | None = None  # publishes GraphCommitted after every write
    knowledge_base: KnowledgeBase | None = None
    entity_index: EntitySemanticIndex | None = None
    embedding_space: str = ""
    embedder: EntityEmbedder | None = None
    resolution_llm: CompletionClient | None = None


class GraphIngestor:
    """Extract → gate → resolve → incrementally write one document's chunks."""

    def __init__(self, deps: GraphIngestDeps) -> None:
        self.graph = deps.graph
        self.review_queue = deps.review_queue
        self.llm = deps.llm
        self.knowledge_base = deps.knowledge_base
        self.schema = deps.schema
        if self.knowledge_base is None or self.knowledge_base.build_mode == "schema":
            self.schema = self.schema or default_schema()
        self._open_runtime = None
        if self.knowledge_base and self.knowledge_base.build_mode == "open":
            from agentic_graphrag.knowledge.open_ingest import OpenRuntime

            self._open_runtime = OpenRuntime(
                knowledge_base=self.knowledge_base,
                index=deps.entity_index,
                space=deps.embedding_space,
                embedder=deps.embedder,
                judge=deps.resolution_llm,
            )
        self.on_commit = deps.on_commit
        self.events = deps.events

    def ingest_chunks(
        self, chunks: list[ChunkRecord], *, tenant_id: str = "", batch_id: str = ""
    ) -> GraphIngestResult:
        if self.knowledge_base and (tenant_id or "default") != self.knowledge_base.tenant_id:
            raise ValueError("Ingest tenant does not match its knowledge base")
        if self._open_runtime is not None:
            from agentic_graphrag.knowledge.open_ingest import ingest_open

            return ingest_open(self, chunks, runtime=self._open_runtime, batch_id=batch_id)
        result = GraphIngestResult(extractor="llm" if self.llm is not None else "rules")
        triples = self._extract(chunks, result)
        result.triples_extracted = len(triples)
        if not triples:
            return result
        if self.knowledge_base and self.knowledge_base.review_policy == "required":
            from agentic_graphrag.knowledge.review.staging import stage_schema_facts

            assert self.schema is not None
            result.conflicts_review, rejected = stage_schema_facts(
                triples,
                self.review_queue,
                schema=self.schema,
                knowledge_base=self.knowledge_base,
                batch_id=batch_id,
            )
            result.rejected += rejected
            return result
        self._canonicalize(triples, tenant_id)
        updater = IncrementalUpdater(
            self.graph,
            schema=self.schema,
            review_queue=self.review_queue,
            tenant_id=tenant_id,
            on_commit=self.on_commit,
            events=self.events,
        )
        batch = updater.apply_batch(triples, batch_id=batch_id or None)
        result.accepted = batch.accepted
        result.rejected = batch.rejected
        result.conflicts_auto = batch.conflicts_auto
        result.conflicts_review = batch.conflicts_review
        result.conflicts_temporal = batch.conflicts_temporal
        result.conflicts_kept = batch.conflicts_kept
        return result

    def _extract(self, chunks: list[ChunkRecord], result: GraphIngestResult) -> list[Triple]:
        triples: list[Triple] = []
        for chunk in chunks:
            extracted = self._extract_chunk(chunk, result)
            for triple in extracted:
                triple.source_doc_id = chunk.doc_id
                triple.source_chunk_id = chunk.chunk_id
                triple.source_doc_version = chunk.metadata.get("document_version")
                triple.source_content_sha256 = str(chunk.metadata.get("content_sha256") or "")
            triples.extend(extracted)
        return triples

    def _extract_chunk(self, chunk: ChunkRecord, result: GraphIngestResult) -> list[Triple]:
        if self.llm is None:
            return extract_rules_from_chunk(chunk)
        from agentic_graphrag.knowledge.extract_core import extract_from_chunk

        try:
            assert self.schema is not None
            accepted, rejected = extract_from_chunk(chunk, self.schema, self.llm)
        except Exception as exc:  # noqa: BLE001 — one bad chunk must not sink the doc
            # Rule extraction still runs, so the doc reaches the graph with
            # weaker triples; the loss must be visible (O-9).
            record_degradation(
                "knowledge_extract", REASON_EXTRACT_FAILED, exc, chunk_id=chunk.chunk_id
            )
            result.error = f"llm_extract_failed: {type(exc).__name__}"
            return extract_rules_from_chunk(chunk)
        result.rejected += len(rejected)
        for triple in accepted:
            triple.source_doc_id = chunk.doc_id
            triple.source_chunk_id = chunk.chunk_id
        return accepted

    def _canonicalize(self, triples: list[Triple], tenant_id: str) -> None:
        """Map surface names onto entities already in the graph (resolution tier 1)."""
        known = self._known_names(tenant_id)
        for triple in triples:
            for mention in (triple.head, triple.tail):
                mention.name = known.get(normalize_key(mention.name), mention.name.strip())

    def _known_names(self, tenant_id: str) -> dict[str, str]:
        if not isinstance(self.graph, EntityLister):
            return {}
        entities = self.graph.list_entities(limit=_ENTITY_SCAN_LIMIT, tenant_id=tenant_id)
        return {normalize_key(e.name): e.name for e in entities}
