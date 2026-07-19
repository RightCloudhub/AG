"""Convert validated triples into graph records and load GraphStore."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from agentic_graphrag.knowledge.schema_check import Triple
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
) -> dict[str, int]:
    entities, relations = triples_to_records(triples)
    if clear_first:
        store.clear()
    n_ent = store.upsert_entities(entities)
    n_rel = store.upsert_relations(relations)
    counts = store.counts()
    return {
        "entities_upserted": n_ent,
        "relations_upserted": n_rel,
        "nodes": counts.get("nodes", 0),
        "relationships": counts.get("relationships", 0),
    }
