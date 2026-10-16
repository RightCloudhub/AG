"""Convert validated triples into graph records and load GraphStore."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore, RelationRecord
from agentic_graphrag.knowledge.schema_check import (
    EntityMention,
    SchemaDefinition,
    Triple,
    ValidationResult,
    default_confidence_threshold,
    default_schema,
    gate_triples,
)
from agentic_graphrag.knowledge.temporal import prefer_incoming


def _resolved_threshold(value: float | None) -> float:
    """``None`` means "use the configured floor", never "accept everything"."""
    return default_confidence_threshold() if value is None else float(value)


def _entity_id(name: str, etype: str) -> str:
    key = f"{etype}:{name.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _relation_id(head_id: str, rel: str, tail_id: str) -> str:
    key = f"{head_id}|{rel}|{tail_id}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


TENANT_KEY = "tenant_id"


def stamp_tenant(triple: Triple, tenant_id: str) -> None:
    """Record the owning tenant on a triple (no-op for the shared/seed scope)."""
    if tenant_id:
        triple.attributes = {**(triple.attributes or {}), TENANT_KEY: tenant_id}


def _tenant_of(triple: Triple) -> str:
    return str((triple.attributes or {}).get(TENANT_KEY) or "")


def triples_to_records(
    triples: list[Triple],
) -> tuple[list[EntityRecord], list[RelationRecord]]:
    entities: dict[tuple[str, str], EntityRecord] = {}
    relations: dict[tuple[str, str], RelationRecord] = {}
    sources_by_entity: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for t in triples:
        tenant = _tenant_of(t)
        source = {
            "doc_id": t.source_doc_id,
            "chunk_id": t.source_chunk_id,
            "span": t.source_span,
            "confidence": t.confidence,
        }
        hid = _add_entity(entities, t.head, tenant)
        tid = _add_entity(entities, t.tail, tenant)
        sources_by_entity[(tenant, hid)].append(source)
        sources_by_entity[(tenant, tid)].append(source)
        _merge_relation(relations, _relation_record(t, (hid, tid), tenant), source)

    for key, ent in entities.items():
        ent.sources = sources_by_entity.get(key, [])

    return list(entities.values()), list(relations.values())


def _add_entity(
    entities: dict[tuple[str, str], EntityRecord], mention: EntityMention, tenant: str
) -> str:
    eid = _entity_id(mention.name, mention.type)
    if (tenant, eid) not in entities:
        entities[(tenant, eid)] = EntityRecord(
            id=eid, name=mention.name.strip(), type=mention.type, tenant_id=tenant
        )
    return eid


def _relation_record(t: Triple, ids: tuple[str, str], tenant: str) -> RelationRecord:
    hid, tid = ids
    return RelationRecord(
        id=_relation_id(hid, t.relation, tid),
        type=t.relation,
        head_id=hid,
        tail_id=tid,
        head_name=t.head.name.strip(),
        tail_name=t.tail.name.strip(),
        confidence=t.confidence,
        attributes={k: v for k, v in (t.attributes or {}).items() if k != TENANT_KEY},
        sources=[],
        tenant_id=tenant,
    )


def _merge_relation(
    relations: dict[tuple[str, str], RelationRecord],
    rec: RelationRecord,
    source: dict[str, Any],
) -> None:
    """Same edge seen twice: the later-dated fact wins, else higher confidence (BL-14)."""
    key = (rec.tenant_id, rec.id)
    current = relations.get(key)
    if current is None or prefer_incoming(
        (rec.attributes, rec.confidence), (current.attributes, current.confidence)
    ):
        rec.sources = [source] + (current.sources if current else [])
        relations[key] = rec
    else:
        current.sources.append(source)


def load_triples_into_graph(
    store: GraphStore,
    triples: list[Triple],
    *,
    clear_first: bool = True,
    schema: SchemaDefinition | None = None,
    confidence_threshold: float | None = None,
    reject_log_path: str | Path | None = None,
    pre_gated: bool = False,
) -> dict[str, Any]:
    """Upsert triples into ``store``, applying the P2-KG-02/03 ingestion gate.

    The gate runs by default: ``schema=None`` means "use the configured domain
    schema", not "skip validation" — the latter reading turned the documented
    invariant into an opt-in (docs/design/BUSINESS_LOGIC.md BL-07). Callers that
    already gated pass ``pre_gated=True`` so rejections are not counted twice.
    """
    gate = (
        None
        if pre_gated
        else _run_gate(
            triples,
            schema,
            confidence_threshold=confidence_threshold,
            reject_log_path=reject_log_path,
        )
    )
    accepted = triples if gate is None else gate.accepted
    entities, relations = triples_to_records(accepted)
    if clear_first:
        store.clear()
    n_ent = store.upsert_entities(entities)
    n_rel = store.upsert_relations(relations)
    counts = store.counts()
    stats: dict[str, Any] = {
        "entities_upserted": n_ent,
        "relations_upserted": n_rel,
        "nodes": counts.get("nodes", 0),
        "relationships": counts.get("relationships", 0),
        "triples_input": len(triples),
        "triples_accepted": len(accepted),
        "triples_rejected": len(gate.rejected) if gate else 0,
    }
    if gate is not None:
        stats["rejection_reasons"] = gate.rejection_reasons
    return stats


def _run_gate(
    triples: list[Triple],
    schema: SchemaDefinition | None,
    *,
    confidence_threshold: float | None,
    reject_log_path: str | Path | None,
) -> ValidationResult:
    """Schema + confidence gate; ``None`` inputs resolve to configured defaults."""
    gate = gate_triples(
        triples,
        schema if schema is not None else default_schema(),
        confidence_threshold=_resolved_threshold(confidence_threshold),
    )
    if reject_log_path is not None and gate.rejected:
        _append_reject_log(reject_log_path, gate)
    return gate


def _append_reject_log(path: str | Path, result: ValidationResult) -> None:
    import json

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for row in result.to_reject_records():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
