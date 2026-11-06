"""Audited ID-based entity merges retain a reversible before-image and redirects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.capabilities import (
    EntityLookup,
    EntityMutation,
    RelationDeletion,
    RelationScanner,
)
from agentic_graphrag.core.ports.entities import EntitySemanticIndex, SemanticEntity
from agentic_graphrag.core.ports.stores import DocStore, EntityRecord, GraphStore, RelationRecord
from agentic_graphrag.knowledge.review.queue import ReviewItem, ReviewQueue

SCAN_PAGE = 1000


@dataclass
class OpenMergeDeps:
    graph: GraphStore
    index: EntitySemanticIndex
    space: str
    queue: ReviewQueue


def apply_open_merge(item: ReviewItem, deps: OpenMergeDeps) -> dict[str, Any]:
    if not item.payload.get("keep_id") or item.payload["keep_id"] not in item.payload.get(
        "candidate_ids", []
    ):
        return {"applied": False, "effect": "ambiguous_selection_required"}
    scope = KnowledgeScope(
        tenant_id=item.tenant_id or "default", kb_id=item.payload.get("kb_id", "default")
    )
    before = item.execution.get("merge_before") or _prepare(item, deps, scope=scope)
    keep, drop = (EntityRecord(**row) for row in before["entities"])
    if not isinstance(deps.graph, EntityMutation):
        raise TypeError("Open entity review needs graph mutation capability")
    merged = replace(
        keep,
        aliases=list(dict.fromkeys([*keep.aliases, *drop.aliases, drop.name, drop.id])),
        sources=_sources(keep.sources, drop.sources),
    )
    deps.graph.upsert_entities([merged])
    deps.graph.rewire_entity(drop.id, keep.id, keep_name=keep.name, tenant_id=scope.tenant_id)
    deps.graph.delete_entity(drop.id, tenant_id=scope.tenant_id)
    if semantic_before := before.get("semantic_entities"):
        semantic = SemanticEntity.model_validate(semantic_before[0])
        deps.index.upsert(
            [
                semantic.model_copy(
                    update={
                        "aliases": tuple(merged.aliases),
                        "sources": tuple(SourceRef.model_validate(raw) for raw in merged.sources),
                    }
                )
            ],
            space=deps.space,
        )
    deps.index.redirect(drop.id, keep.id, scope=scope, space=deps.space, operation_id=item.id)
    return {
        "applied": True,
        "effect": "entities_merged",
        "kept_id": keep.id,
        "dropped_id": drop.id,
        "merge_before": before,
        "after_fingerprint": _fingerprint(
            _capture(deps.graph, scope=scope, ids=(keep.id, drop.id))
        ),
    }


def _prepare(item: ReviewItem, deps: OpenMergeDeps, *, scope: KnowledgeScope) -> dict[str, Any]:
    ids = (str(item.payload["keep_id"]), str(item.payload["drop_id"]))
    before = _capture(deps.graph, scope=scope, ids=ids)
    if len(before["entities"]) != 2:
        raise ValueError("Reviewed entities are unavailable")
    before["semantic_entities"] = []
    for identity in ids:
        semantic = deps.index.get(identity, scope=scope, space=deps.space)
        if semantic is None:
            raise ValueError("Reviewed semantic entity is unavailable")
        before["semantic_entities"].append(semantic.model_dump(mode="json"))
    prepared = {"applied": False, "effect": "merge_prepared", "merge_before": before}
    deps.queue.record_execution(item.id, prepared)
    item.execution = prepared
    return before


def undo_open_merge(item: ReviewItem, deps: OpenMergeDeps, *, docs: DocStore) -> dict[str, Any]:
    execution = item.execution
    if execution.get("effect") != "entities_merged":
        raise ValueError("Only an applied open merge can be reverted")
    before = dict(execution["merge_before"])
    entities = [EntityRecord(**raw) for raw in before["entities"]]
    scope = KnowledgeScope(tenant_id=entities[0].tenant_id, kb_id=entities[0].kb_id)
    ids = (str(execution["kept_id"]), str(execution["dropped_id"]))
    if _fingerprint(_capture(deps.graph, scope=scope, ids=ids)) != execution["after_fingerprint"]:
        raise ValueError("Related graph facts changed after this merge; review a new split")
    _check_sources(before, docs=docs)
    if not isinstance(deps.graph, RelationDeletion):
        raise TypeError("Open merge undo requires scoped relation deletion")
    for raw in before["relations"]:
        deps.graph.delete_relation(str(raw["id"]), tenant_id=scope.tenant_id)
    deps.graph.upsert_entities(entities)
    deps.graph.upsert_relations([RelationRecord(**raw) for raw in before["relations"]])
    if semantic_before := before.get("semantic_entities"):
        deps.index.upsert(
            [SemanticEntity.model_validate(raw) for raw in semantic_before], space=deps.space
        )
    deps.index.remove_redirect(ids[1], scope=scope, space=deps.space, operation_id=item.id)
    outcome = {**execution, "applied": False, "effect": "merge_reverted"}
    deps.queue.record_execution(item.id, outcome)
    item.execution = outcome
    return outcome


def _capture(graph: GraphStore, *, scope: KnowledgeScope, ids: tuple[str, str]) -> dict[str, Any]:
    if not isinstance(graph, EntityLookup) or not isinstance(graph, RelationScanner):
        raise TypeError("Open merge review requires complete graph scans and ID lookup")
    entities = [
        e for identity in ids if (e := graph.get_entity_by_id(identity, tenant_id=scope.tenant_id))
    ]
    if any(e.kb_id != scope.kb_id for e in entities):
        raise ValueError("Entity is outside the reviewed knowledge scope")
    relations = _related_relations(graph, scope=scope, ids=ids)
    return {"entities": [asdict(e) for e in entities], "relations": relations}


def _related_relations(
    graph: RelationScanner, *, scope: KnowledgeScope, ids: tuple[str, str]
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    offset = 0
    while batch := graph.scan_relations(tenant_id=scope.tenant_id, limit=SCAN_PAGE, offset=offset):
        relations.extend(asdict(r) for r in batch if r.head_id in ids or r.tail_id in ids)
        offset += len(batch)
    return sorted(relations, key=lambda r: r["id"])


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _sources(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({json.dumps(row, sort_keys=True): row for row in [*left, *right]}.values())


def _check_sources(before: dict[str, Any], *, docs: DocStore) -> None:
    for entity in before["entities"]:
        for raw in entity["sources"]:
            ref = SourceRef.model_validate(raw)
            doc = docs.get(ref.doc_id, tenant_id=ref.scope.tenant_id)
            if doc is None or doc.version != ref.doc_version or doc.metadata.get("lifecycle"):
                raise ValueError("Cannot restore entities whose source is no longer visible")
