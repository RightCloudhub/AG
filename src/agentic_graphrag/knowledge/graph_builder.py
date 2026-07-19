"""Convert validated triples into graph records and load GraphStore."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentic_graphrag.knowledge.schema_check import (
    SchemaDefinition,
    Triple,
    ValidationResult,
    gate_triples,
)
from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore, RelationRecord


def _entity_id(name: str, etype: str) -> str:
    key = f"{etype}:{name.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _relation_id(head_id: str, rel: str, tail_id: str) -> str:
    key = f"{head_id}|{rel}|{tail_id}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def triples_to_records(
    triples: list[Triple],
) -> tuple[list[EntityRecord], list[RelationRecord]]:
    entities: dict[str, EntityRecord] = {}
    relations: dict[str, RelationRecord] = {}
    sources_by_entity: dict[str, list[dict]] = defaultdict(list)

    for t in triples:
        hid = _entity_id(t.head.name, t.head.type)
        tid = _entity_id(t.tail.name, t.tail.type)
        source = {
            "doc_id": t.source_doc_id,
            "chunk_id": t.source_chunk_id,
            "span": t.source_span,
            "confidence": t.confidence,
        }
        if hid not in entities:
            entities[hid] = EntityRecord(id=hid, name=t.head.name.strip(), type=t.head.type)
        if tid not in entities:
            entities[tid] = EntityRecord(id=tid, name=t.tail.name.strip(), type=t.tail.type)
        sources_by_entity[hid].append(source)
        sources_by_entity[tid].append(source)

        rid = _relation_id(hid, t.relation, tid)
        if rid not in relations or t.confidence > relations[rid].confidence:
            relations[rid] = RelationRecord(
                id=rid,
                type=t.relation,
                head_id=hid,
                tail_id=tid,
                head_name=t.head.name.strip(),
                tail_name=t.tail.name.strip(),
                confidence=t.confidence,
                attributes=t.attributes or {},
                sources=[source],
            )
        else:
            relations[rid].sources.append(source)

    for eid, ent in entities.items():
        ent.sources = sources_by_entity.get(eid, [])

    return list(entities.values()), list(relations.values())


def load_triples_into_graph(
    store: GraphStore,
    triples: list[Triple],
    *,
    clear_first: bool = True,
    schema: SchemaDefinition | None = None,
    confidence_threshold: float | None = None,
    reject_log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Upsert triples into ``store``.

    When ``schema`` is provided, applies the P2-KG-02/03 ingestion gate
    (schema + optional confidence threshold) and never writes rejected
    triples. Rejections are optionally appended to ``reject_log_path``.
    """
    gate: ValidationResult | None = None
    accepted = triples
    if schema is not None:
        thr = 0.0 if confidence_threshold is None else float(confidence_threshold)
        gate = gate_triples(triples, schema, confidence_threshold=thr)
        accepted = gate.accepted
        if reject_log_path is not None and gate.rejected:
            _append_reject_log(reject_log_path, gate)

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


def _append_reject_log(path: str | Path, result: ValidationResult) -> None:
    import json

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for row in result.to_reject_records():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
