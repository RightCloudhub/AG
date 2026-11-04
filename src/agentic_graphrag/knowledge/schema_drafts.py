"""Deduplicated support and conservative proposals from a fixed open graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from agentic_graphrag.core.knowledge import KnowledgeBase, SourceRef
from agentic_graphrag.core.ports.capabilities import RelationScanner
from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore, RelationRecord
from agentic_graphrag.core.schema_drafts import RelationProposal, SchemaDraft

SCAN_PAGE = 1000
MAX_DRAFT_EXAMPLES = 5


def scan_graph(
    graph: GraphStore, *, tenant_id: str
) -> tuple[list[EntityRecord], list[RelationRecord]]:
    entities, relations, offset = [], [], 0
    while batch := graph.list_entities(tenant_id=tenant_id, limit=SCAN_PAGE, offset=offset):
        entities.extend(batch)
        offset += len(batch)
    if not isinstance(graph, RelationScanner):
        raise TypeError("Schema drafting needs complete relation scans")
    offset = 0
    while edges := graph.scan_relations(tenant_id=tenant_id, limit=SCAN_PAGE, offset=offset):
        relations.extend(edges)
        offset += len(edges)
    return entities, relations


def graph_fingerprint(entities: list[EntityRecord], relations: list[RelationRecord]) -> str:
    payload = [
        sorted([asdict(e) for e in entities], key=lambda e: e["id"]),
        sorted([asdict(r) for r in relations], key=lambda r: r["id"]),
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def draft_from_graph(
    graph: GraphStore, kb: KnowledgeBase, *, aliases: dict[str, str] | None = None
) -> SchemaDraft:
    if kb.build_mode != "open":
        raise ValueError("Schema drafts can only be derived from an open graph")
    entities, relations = scan_graph(graph, tenant_id=kb.tenant_id)
    fingerprint = graph_fingerprint(entities, relations)
    by_id = {entity.id: entity for entity in entities}
    groups: dict[str, list[RelationRecord]] = {}
    for relation in relations:
        canonical = (aliases or {}).get(relation.type, relation.type)
        groups.setdefault(canonical, []).append(relation)
    proposals = [_proposal(name, rows, by_id=by_id) for name, rows in groups.items()]
    proposals.sort(key=lambda p: (-p.document_support, -p.distinct_pairs, p.predicate))
    entity_types = _entity_types(entities)
    entity_map = {name: _safe_key("TYPE", name) for name in entity_types}
    relation_map = {
        r.type: _safe_key("REL", (aliases or {}).get(r.type, r.type)) for r in relations
    }
    schema = _schema(proposals, entity_map=entity_map, relation_map=relation_map)
    identity = json.dumps(
        [kb.scope.model_dump(), kb.active_generation, fingerprint, aliases or {}], sort_keys=True
    )
    draft_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return SchemaDraft(
        draft_id=f"draft-{draft_id}",
        scope=kb.scope,
        generation=kb.active_generation,
        input_fingerprint=fingerprint,
        relations=tuple(proposals),
        entity_types=entity_types,
        relation_mapping=relation_map,
        entity_mapping=entity_map,
        schema_definition=schema,
    )


def _proposal(
    name: str, rows: list[RelationRecord], *, by_id: dict[str, EntityRecord]
) -> RelationProposal:
    _check_endpoints(rows, by_id)
    sources = _source_records(rows)
    examples = list({ref.model_dump_json(): ref for ref in sources}.values())[:MAX_DRAFT_EXAMPLES]
    return RelationProposal(
        predicate=name,
        aliases=tuple(sorted({r.type for r in rows})),
        head_types=tuple(sorted({by_id[r.head_id].type for r in rows})),
        tail_types=tuple(sorted({by_id[r.tail_id].type for r in rows})),
        document_ids=tuple(sorted({source.doc_id for source in sources})),
        distinct_pairs=len({(r.head_id, r.tail_id) for r in rows}),
        relation_ids=tuple(sorted({row.id for row in rows})),
        descriptions=tuple(sorted({str(row.attributes.get("description", "")) for row in rows})),
        examples=tuple(examples),
    )


def _entity_types(entities: list[EntityRecord]) -> tuple[str, ...]:
    return tuple(sorted({entity.type for entity in entities}))


def _check_endpoints(rows: list[RelationRecord], by_id: dict[str, EntityRecord]) -> None:
    if any(row.head_id not in by_id or row.tail_id not in by_id for row in rows):
        raise ValueError("Open relation has a missing entity endpoint")


def _source_records(rows: list[RelationRecord]) -> list[SourceRef]:
    return [SourceRef.model_validate(raw) for row in rows for raw in row.sources]


def _safe_key(prefix: str, name: str) -> str:
    return f"{prefix}_{hashlib.sha256(name.encode()).hexdigest()[:12].upper()}"


def _schema(
    proposals: list[RelationProposal], *, entity_map: dict[str, str], relation_map: dict[str, str]
) -> dict[str, Any]:
    relations: dict[str, Any] = {}
    for proposal in proposals:
        key = relation_map[proposal.aliases[0]]
        relations[key] = {
            "head": [entity_map[t] for t in proposal.head_types],
            "tail": [entity_map[t] for t in proposal.tail_types],
            "description": "; ".join(proposal.descriptions),
        }
    return {
        "name": "proposed",
        "version": "draft",
        "entity_types": {target: {"description": source} for source, target in entity_map.items()},
        "relation_types": relations,
    }
