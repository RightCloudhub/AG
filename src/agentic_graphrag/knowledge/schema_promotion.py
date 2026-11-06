"""Reviewed open-to-Schema conversion uses a hidden generation and an atomic switch."""

from __future__ import annotations

from dataclasses import dataclass, replace

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.ports.capabilities import EntityMutation, RelationDeletion
from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore, RelationRecord
from agentic_graphrag.core.schema_drafts import DraftApproval, SchemaDraft, SchemaRegistry
from agentic_graphrag.knowledge.generation_pipeline import GenerationPipeline
from agentic_graphrag.knowledge.schema_check import (
    EntityMention,
    SchemaDefinition,
    Triple,
    validate_triple,
)
from agentic_graphrag.knowledge.schema_drafts import graph_fingerprint, scan_graph
from agentic_graphrag.knowledge.schema_registry import schema_from_mapping
from agentic_graphrag.stores.snapshot_bundle import snapshot_bundle


def approve_draft(
    registry: SchemaRegistry, scope: KnowledgeScope, draft_id: str, *, approval: DraftApproval
) -> SchemaDraft:
    draft = registry.get_draft(scope, draft_id)
    if draft is None:
        raise KeyError("Unknown Schema draft")
    schema = schema_from_mapping(approval.schema_definition)
    predicates = {alias for proposal in draft.relations for alias in proposal.aliases}
    if not set(approval.relation_mapping).issubset(predicates):
        raise ValueError("Relation mapping references predicates outside this draft")
    if not set(approval.entity_mapping).issubset(draft.entity_types):
        raise ValueError("Entity mapping references types outside this draft")
    if not set(approval.relation_mapping.values()).issubset(schema.relation_types):
        raise ValueError("Relation mapping references undeclared Schema relations")
    if not set(approval.entity_mapping.values()).issubset(schema.entity_types):
        raise ValueError("Entity mapping references undeclared Schema types")
    return registry.approve(scope, draft_id, approval=approval)


@dataclass
class SchemaPromoter:
    pipeline: GenerationPipeline
    registry: SchemaRegistry

    def promote(self, scope: KnowledgeScope, draft_id: str, *, operation_id: str) -> KnowledgeBase:
        draft = self.registry.get_draft(scope, draft_id)
        kb = self.pipeline.metadata.get(scope)
        if draft is None or kb is None or draft.status not in {"approved", "published"}:
            raise ValueError("Schema promotion requires a human-approved draft")
        if not draft.reviewer.strip() or not draft.schema_id or not draft.schema_version:
            raise ValueError("Schema approval is incomplete")
        if kb.active_generation != draft.generation:
            return self._completed(scope, draft, operation_id=operation_id)
        schema = schema_from_mapping(draft.schema_definition)
        write = self.pipeline.prepare(scope, operation_id=operation_id)
        source = snapshot_bundle(self.pipeline.metadata, scope, write.source_generation)
        entities, relations = scan_graph(source.graph, tenant_id=scope.tenant_id)
        if graph_fingerprint(entities, relations) != draft.input_fingerprint:
            raise ValueError("Open graph changed after Schema review; create a new draft")
        converted = _convert(entities, relations, draft=draft, schema=schema)
        _replace_graph(write.bundle.graph, *converted, tenant_id=scope.tenant_id)
        target = kb.model_copy(
            update={
                "build_mode": "schema",
                "schema_id": draft.schema_id,
                "schema_version": draft.schema_version,
                "rule_planner_profile": None,
            }
        )
        published = self.pipeline.complete(write, target_config=target)
        self.registry.mark_published(scope, draft_id, generation=published.active_generation)
        return published

    def _completed(
        self, scope: KnowledgeScope, draft: SchemaDraft, *, operation_id: str
    ) -> KnowledgeBase:
        intent = self.pipeline.metadata.get_write(scope, operation_id)
        kb = self.pipeline.metadata.get(scope)
        if (
            intent is None
            or intent.status != "completed"
            or kb is None
            or kb.active_generation != intent.target_generation
            or kb.schema_id != draft.schema_id
            or kb.schema_version != draft.schema_version
        ):
            raise ValueError("Schema draft does not describe the active graph generation")
        self.registry.mark_published(scope, draft.draft_id, generation=kb.active_generation)
        return kb


def _convert(
    entities: list[EntityRecord],
    relations: list[RelationRecord],
    *,
    draft: SchemaDraft,
    schema: SchemaDefinition,
) -> tuple[list[EntityRecord], list[RelationRecord]]:
    _check_unmapped(entities, relations, draft=draft)
    mapped_entities = [
        replace(
            e,
            type=draft.entity_mapping[e.type],
            attributes={**e.attributes, "graph_mode": "schema"},
        )
        for e in entities
    ]
    mapped_relations = [
        replace(
            r,
            type=draft.relation_mapping[r.type],
            attributes={**r.attributes, "graph_mode": "schema"},
        )
        for r in relations
    ]
    by_id = {row.id: row for row in mapped_entities}
    for relation in mapped_relations:
        head, tail = by_id[relation.head_id], by_id[relation.tail_id]
        triple = Triple(
            head=EntityMention(name=head.name, type=head.type),
            relation=relation.type,
            tail=EntityMention(name=tail.name, type=tail.type),
            confidence=relation.confidence,
        )
        if reason := validate_triple(triple, schema):
            raise ValueError(f"Reviewed Schema mapping is invalid: {reason}")
    return mapped_entities, mapped_relations


def _check_unmapped(
    entities: list[EntityRecord], relations: list[RelationRecord], *, draft: SchemaDraft
) -> None:
    unmapped = [row.id for row in relations if row.type not in draft.relation_mapping]
    unmapped.extend(row.id for row in entities if row.type not in draft.entity_mapping)
    if unmapped:
        raise ValueError(f"Schema mapping leaves {len(unmapped)} facts/entities unresolved")


def _replace_graph(
    graph: GraphStore,
    entities: list[EntityRecord],
    relations: list[RelationRecord],
    *,
    tenant_id: str,
) -> None:
    if not isinstance(graph, RelationDeletion) or not isinstance(graph, EntityMutation):
        raise TypeError("Schema promotion requires scoped graph mutation capabilities")
    for relation in relations:
        graph.delete_relation(relation.id, tenant_id=tenant_id)
    for entity in entities:
        graph.delete_entity(entity.id, tenant_id=tenant_id)
    if graph.upsert_entities(entities) != len(entities) or graph.upsert_relations(relations) != len(
        relations
    ):
        raise RuntimeError("Incomplete Schema shadow generation")
