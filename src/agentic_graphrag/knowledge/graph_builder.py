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
    default_confidence_threshold,
    default_schema,
    gate_triples,
)
from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore, RelationRecord


def _resolved_threshold(value: float | None) -> float:
    """``None`` means "use the configured floor", never "accept everything"."""
    return default_confidence_threshold() if value is None else float(value)


def _entity_id(name: str, etype: str) -> str:
    key = f"{etype}:{name.strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _relation_id(
    head_id: str,
    rel: str,
    tail_id: str,
    *,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> str:
    key = f"{head_id}|{rel}|{tail_id}"
    # Temporal edges (ADR-007 / BL-14): the window is part of the identity so
    # the same fact over disjoint periods coexists instead of overwriting.
    if valid_from or valid_to:
        key = f"{key}|{valid_from or ''}~{valid_to or ''}"
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

        rid = _relation_id(hid, t.relation, tid, valid_from=t.valid_from, valid_to=t.valid_to)
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
                valid_from=t.valid_from,
                valid_to=t.valid_to,
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
    pre_gated: bool = False,
) -> dict[str, Any]:
    """Upsert triples into ``store``, applying the P2-KG-02/03 ingestion gate.

    The gate runs by default: ``schema=None`` means "use the configured domain
    schema", not "skip validation" — the latter reading turned the documented
    invariant into an opt-in (docs/BUSINESS_LOGIC.md BL-07). Callers that
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
