"""Directed source facts projected onto a reproducible weighted undirected graph."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from agentic_graphrag.core.communities import GraphProjection, ProjectedEdge, ProjectedNode
from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def source_documents(sources: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted({str(source["doc_id"]) for source in sources if source.get("doc_id")}))


def semantic_record(record: EntityRecord | RelationRecord) -> dict[str, Any]:
    data = asdict(record)
    for name in ("tenant_id", "kb_id", "generation"):
        data.pop(name, None)
    data["sources"] = sorted(data.get("sources", []), key=fingerprint)
    return data


def project_graph(
    scope: KnowledgeScope,
    generation: str,
    *,
    entities: Sequence[EntityRecord],
    relations: Sequence[RelationRecord],
    document_hashes: Mapping[str, str] | None = None,
) -> GraphProjection:
    hashes = document_hashes or {}
    entity_map = {entity.id: entity for entity in entities}
    if len(entity_map) != len(entities):
        raise ValueError("Projection contains duplicate canonical entity IDs")
    docs: dict[str, set[str]] = {
        entity.id: set(source_documents(entity.sources)) for entity in entities
    }
    grouped, related = _relations(relations, scope, entities=entity_map, docs=docs)
    nodes = tuple(
        _node(entity_map[key], scope, docs=docs[key], hashes=hashes, facts=related[key])
        for key in sorted(entity_map)
    )
    edges = tuple(_edge(key, grouped[key], hashes=hashes) for key in sorted(grouped))
    return GraphProjection(scope=scope, generation=generation, nodes=nodes, edges=edges)


def _relations(
    relations: Sequence[RelationRecord],
    scope: KnowledgeScope,
    *,
    entities: dict[str, EntityRecord],
    docs: dict[str, set[str]],
) -> tuple[dict[tuple[str, str], list[RelationRecord]], dict[str, set[str]]]:
    grouped: dict[tuple[str, str], list[RelationRecord]] = defaultdict(list)
    related: dict[str, set[str]] = defaultdict(set)
    for relation in relations:
        _check_scope(relation, scope)
        if relation.head_id not in entities or relation.tail_id not in entities:
            raise ValueError("Graph projection contains an unresolved relation endpoint")
        for endpoint in {relation.head_id, relation.tail_id}:
            docs[endpoint].update(source_documents(relation.sources))
            related[endpoint].add(fingerprint(semantic_record(relation)))
        if relation.head_id != relation.tail_id:
            key = tuple(sorted((relation.head_id, relation.tail_id)))
            grouped[(key[0], key[1])].append(relation)
    return grouped, related


def _check_scope(record: EntityRecord | RelationRecord, scope: KnowledgeScope) -> None:
    if (record.tenant_id or "default", record.kb_id) != (scope.tenant_id, scope.kb_id):
        raise ValueError("Community projection contains another knowledge scope")


def _node(
    entity: EntityRecord,
    scope: KnowledgeScope,
    *,
    docs: set[str],
    hashes: Mapping[str, str],
    facts: set[str],
) -> ProjectedNode:
    _check_scope(entity, scope)
    doc_ids = tuple(sorted(docs))
    return ProjectedNode(
        id=entity.id,
        document_ids=doc_ids,
        content_fingerprint=fingerprint(
            [semantic_record(entity), sorted(facts), [(d, hashes.get(d, "")) for d in doc_ids]]
        ),
    )


def _edge(
    key: tuple[str, str], relations: list[RelationRecord], *, hashes: Mapping[str, str]
) -> ProjectedEdge:
    docs = tuple(sorted({d for row in relations for d in source_documents(row.sources)}))
    # Content-identical documents and overlapping chunks contribute at most one vote.
    support = {hashes.get(doc_id) or doc_id for doc_id in docs}
    weight = float(max(1, len(support)))
    facts = sorted((semantic_record(row) for row in relations), key=fingerprint)
    return ProjectedEdge(
        source=key[0],
        target=key[1],
        weight=weight,
        document_ids=docs,
        relation_ids=tuple(sorted({row.id for row in relations})),
        content_fingerprint=fingerprint([facts, [(d, hashes.get(d, "")) for d in docs]]),
    )
