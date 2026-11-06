"""Build open graph records only from server-validated source references."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from agentic_graphrag.core.knowledge import SourceRef
from agentic_graphrag.core.ports.entities import EntitySemanticIndex
from agentic_graphrag.core.ports.stores import GraphStore, RelationRecord
from agentic_graphrag.knowledge.open_contracts import OpenRelation
from agentic_graphrag.knowledge.open_resolution import ResolvedEntity
from agentic_graphrag.knowledge.review.queue import ReviewQueue


def relation_record(
    relation: OpenRelation, source: SourceRef, *, entities: dict[str, ResolvedEntity]
) -> RelationRecord:
    head, tail = entities[relation.head].record, entities[relation.tail].record
    key = [head.id, relation.predicate, tail.id, source.model_dump(mode="json")]
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:32]
    return RelationRecord(
        id=f"relation-{digest}",
        type=relation.predicate,
        head_id=head.id,
        tail_id=tail.id,
        head_name=head.name,
        tail_name=tail.name,
        confidence=relation.confidence,
        attributes={"graph_mode": "open", "description": relation.description},
        sources=[source.model_dump(mode="json")],
        tenant_id=source.scope.tenant_id,
        kb_id=source.scope.kb_id,
    )


def write_records(
    graph: GraphStore,
    entities: list[ResolvedEntity],
    relations: list[RelationRecord],
    *,
    index: EntitySemanticIndex,
    space: str,
) -> None:
    # Persist canonical decisions before graph writes so a failed write can resume
    # without asking a model to make a different identity decision for the same mention.
    index.upsert([item.semantic for item in entities], space=space)
    for entity in entities:
        index.bind_mention(entity.mention_id, entity.record.id, source=entity.source, space=space)
    unique = {item.record.id: item.record for item in entities}
    if graph.upsert_entities(list(unique.values())) != len(unique):
        raise RuntimeError("Incomplete open entity write")
    if graph.upsert_relations(relations) != len(relations):
        raise RuntimeError("Incomplete open relation write")


def stage_resolution(
    entities: list[ResolvedEntity], queue: ReviewQueue | None, *, batch_id: str
) -> int:
    pending = 0
    for entity in entities:
        if not entity.review_candidates:
            continue
        if queue is None:
            raise ValueError("Ambiguous open entity resolution requires a review queue")
        key = f"resolve-{entity.mention_id}"
        if queue.get(key) is None:
            queue.enqueue(
                "resolution",
                {
                    "open_resolution": True,
                    "drop_id": entity.record.id,
                    "candidate_ids": list(entity.review_candidates),
                    "source_refs": [entity.source.model_dump(mode="json")],
                    "kb_id": entity.source.scope.kb_id,
                    "keep_id": entity.review_candidates[0]
                    if len(entity.review_candidates) == 1
                    else None,
                },
                item_id=key,
                batch_id=batch_id,
                tenant_id=entity.source.scope.tenant_id,
            )
        pending += 1
    return pending


def staged_payload(
    entities: list[ResolvedEntity], relations: list[RelationRecord]
) -> dict[str, Any]:
    return {
        "open_graph": True,
        "entities": [asdict(item.record) for item in entities],
        "relations": [asdict(item) for item in relations],
        "source_refs": [item.source.model_dump(mode="json") for item in entities],
    }
