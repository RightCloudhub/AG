"""Open-domain AG index preparation for the public benchmark (B-03).

Builds one immutable prepared index per dataset corpus inside a fresh isolated
bundle: passage text → vector + BM25 + graph (benchmark-only schema, production
gate rules). Gold answers and support labels are never passed in. Extraction
failures are counted per passage (quarantine), not silently dropped.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_graphrag.app.wiring import _entities_from_triples
from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.ports.stores import ChunkRecord
from agentic_graphrag.eval.public.accounting import UsageLedger, ledger_from_budget
from agentic_graphrag.eval.public.alignment import relation_key
from agentic_graphrag.eval.public.composition import isolated_bundle
from agentic_graphrag.eval.public.records import Passage
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import (
    SchemaDefinition,
    Triple,
    gate_triples,
    load_schema,
)
from agentic_graphrag.stores.factory import StoreBundle

# Raw triple producer; the production schema + confidence gate is applied here, uniformly.
Extractor = Callable[[ChunkRecord], list[Triple]]
EMBED_BATCH = 64
INDEX_MAX_CALLS = 10_000_000
INDEX_MAX_TOKENS = 10_000_000_000


@dataclass(frozen=True)
class PreparedIndex:
    """Shared, read-only AG index for one dataset corpus (both AG arms use it)."""

    bundle: StoreBundle
    passage_ids: frozenset[str]
    corpus_sha256: str
    known_entities: tuple[str, ...]
    relation_sources: dict[str, tuple[str, ...]]
    passages_with_triples: frozenset[str]
    stats: dict[str, Any] = field(default_factory=dict)
    usage: UsageLedger = field(default_factory=lambda: UsageLedger(scope="indexing"))


def passage_chunks(passages: list[Passage]) -> list[ChunkRecord]:
    """Canonical passage = one chunk; chunk_id/doc_id are the canonical passage id."""
    return [
        ChunkRecord(
            chunk_id=p.passage_id,
            doc_id=p.passage_id,
            text=p.index_text(),
            index=0,
            metadata={"title": p.title, "dataset": p.dataset.value},
        )
        for p in passages
    ]


def llm_extractor(schema: SchemaDefinition, llm: LLMClient) -> Extractor:
    """Live extraction with the benchmark schema in the production extraction prompt."""
    from agentic_graphrag.knowledge.extract_core import llm_extract_raw, stamp_provenance

    def run(chunk: ChunkRecord) -> list[Triple]:
        triples = llm_extract_raw(chunk, schema, llm).triples
        for triple in triples:
            stamp_provenance(triple, chunk)
        return triples

    return run


def prepare_ag_index(
    passages: list[Passage],
    *,
    corpus_sha256: str,
    embedder: LLMClient,
    extractor: Extractor,
    gate: GateSpec,
    index_budget: BudgetTracker | None = None,
) -> PreparedIndex:
    t0 = time.perf_counter()
    bundle = isolated_bundle()
    chunks = passage_chunks(passages)
    _embed(chunks, embedder)
    if chunks and chunks[0].embedding:
        bundle.vector.ensure_collection(len(chunks[0].embedding))
    bundle.vector.upsert(chunks)
    bundle.fulltext.index(chunks)
    accepted, stats = _extract_all(chunks, extractor, gate)
    load_triples_into_graph(bundle.graph, accepted, clear_first=False, pre_gated=True)
    elapsed = (time.perf_counter() - t0) * MILLISECONDS_PER_SECOND
    snapshot = (index_budget or BudgetTracker()).snapshot()
    usage = ledger_from_budget(None, snapshot, elapsed_ms=elapsed).model_copy(
        update={"scope": "indexing"}
    )
    sources = _relation_sources(accepted)
    return PreparedIndex(
        bundle=bundle,
        passage_ids=frozenset(p.passage_id for p in passages),
        corpus_sha256=corpus_sha256,
        known_entities=tuple(_entities_from_triples(accepted)),
        relation_sources=sources,
        passages_with_triples=frozenset(t.source_chunk_id for t in accepted if t.source_chunk_id),
        stats={**stats, "graph": bundle.graph.counts()},
        usage=usage,
    )


def _embed(chunks: list[ChunkRecord], embedder: LLMClient) -> None:
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        vectors = embedder.embed_many([c.text for c in batch])
        for chunk, vec in zip(batch, vectors, strict=True):
            chunk.embedding = vec


@dataclass(frozen=True)
class GateSpec:
    schema: SchemaDefinition
    confidence_threshold: float


def _extract_all(
    chunks: list[ChunkRecord], extractor: Extractor, gate: GateSpec
) -> tuple[list[Triple], dict[str, Any]]:
    accepted: list[Triple] = []
    reasons: dict[str, int] = defaultdict(int)
    failed: list[str] = []
    rejected_n = 0
    for chunk in chunks:
        try:
            raw = extractor(chunk)
        except Exception:  # noqa: BLE001 — quarantined and counted, never fatal
            failed.append(chunk.chunk_id)
            continue
        for triple in raw:
            triple.source_chunk_id = triple.source_chunk_id or chunk.chunk_id
            triple.source_doc_id = triple.source_doc_id or chunk.doc_id
        result = gate_triples(raw, gate.schema, confidence_threshold=gate.confidence_threshold)
        accepted.extend(result.accepted)
        rejected_n += len(result.rejected)
        for bucket, n in result.rejection_reasons.items():
            reasons[bucket] += n
    total = len(accepted) + rejected_n
    return accepted, {
        "triples_input": total,
        "triples_accepted": len(accepted),
        "triples_rejected": rejected_n,
        "schema_rejection_rate": (rejected_n / total) if total else None,
        "rejection_reasons": dict(reasons),
        "extraction_failures": len(failed),
        "failed_passages": failed[:50],
    }


def _relation_sources(triples: list[Triple]) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = defaultdict(list)
    for t in triples:
        if t.source_chunk_id:
            out[relation_key(t.head.name, t.relation, t.tail.name)].append(t.source_chunk_id)
    return {k: tuple(dict.fromkeys(v)) for k, v in out.items()}


def graph_coverage(index: PreparedIndex, supporting: set[str]) -> float | None:
    """Share of gold supporting passages that contributed ≥1 accepted triple (B-00)."""
    if not supporting:
        return None
    return len(supporting & index.passages_with_triples) / len(supporting)


def load_bench_schema(path: Path) -> SchemaDefinition:
    schema = load_schema(path)
    if schema.name == "corporate_relations_v0":
        raise ValueError("the public benchmark must not use the production domain schema")
    return schema
