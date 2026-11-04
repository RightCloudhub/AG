"""Required human review stores valid candidates without making facts searchable."""

from __future__ import annotations

import hashlib
import json

from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.knowledge.graph_builder import stamp_tenant
from agentic_graphrag.knowledge.review.queue import ReviewQueue, ReviewStatus
from agentic_graphrag.knowledge.schema_check import (
    SchemaDefinition,
    Triple,
    default_confidence_threshold,
    gate_triples,
)


def stage_schema_facts(
    triples: list[Triple],
    queue: ReviewQueue | None,
    *,
    schema: SchemaDefinition,
    knowledge_base: KnowledgeBase,
    batch_id: str,
) -> tuple[int, int]:
    if queue is None:
        raise ValueError("Required Schema review needs a durable review queue")
    gate = gate_triples(triples, schema, confidence_threshold=default_confidence_threshold())
    pending = 0
    for triple in gate.accepted:
        stamp_tenant(triple, knowledge_base.tenant_id)
        payload = {
            "triple": triple.model_dump(mode="json"),
            "staged": True,
            "kb_id": knowledge_base.kb_id,
            "schema_version": schema.version,
            "config_revision": knowledge_base.config_revision,
        }
        identity = [knowledge_base.scope.model_dump(), payload]
        key = (
            "stage-"
            + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
        )
        item = queue.get(key)
        if item is None:
            item = queue.enqueue(
                "extraction",
                payload,
                item_id=key,
                tenant_id=knowledge_base.tenant_id,
                confidence=triple.confidence,
                batch_id=batch_id,
            )
        pending += item.status == ReviewStatus.PENDING
    return pending, len(gate.rejected)
