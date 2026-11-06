"""Neighbor-candidate assembly and edge labels for :class:`GraphRetriever`.

Split out of :mod:`agentic_graphrag.retrieval.graph` so that module stays under
the 300-line code-metrics gate. Nothing here touches the store or scoring: these
are the pure helpers that turn ranked beam edges into :class:`Candidate`s and
their display labels.
"""

from __future__ import annotations

from collections.abc import Iterator

from agentic_graphrag.core.evidence import Candidate, CandidateSource, Citation
from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord
from agentic_graphrag.knowledge.temporal import validity_label
from agentic_graphrag.retrieval.graph_beam import BeamItem
from agentic_graphrag.retrieval.graph_relation import normalize_name
from agentic_graphrag.retrieval.provenance import source_chunk_ids, source_citations

__all__ = [
    "apply_seed_boost",
    "edge_labels",
    "iter_beam_edges",
    "names_touch_seed",
    "neighbor_candidates",
]


def iter_beam_edges(beams: list[BeamItem]) -> Iterator[tuple[RelationRecord, EntityRecord]]:
    seen: set[tuple[int, int]] = set()
    for item in beams:
        for edge, entity in item.edges:
            key = (id(edge), id(entity))
            if key not in seen:
                seen.add(key)
                yield edge, entity


def edge_labels(rel: RelationRecord, ent: EntityRecord, entity_name: str) -> tuple[str, str, str]:
    head = rel.head_name or entity_name
    tail = rel.tail_name or ent.name
    if not rel.head_name and not rel.tail_name:
        head, tail = entity_name, ent.name
    content = f"{head} -[{rel.type}]-> {tail} ({ent.type}){validity_label(rel.attributes)}"
    return head, tail, content


def apply_seed_boost(
    score: float,
    rel: RelationRecord,
    *,
    ends: tuple[str, str],
    seed_norm: str,
) -> float:
    names = (*ends, rel.head_name or "", rel.tail_name or "")
    if names_touch_seed(names, seed_norm):
        return score + 1.0
    return score * 0.2


def names_touch_seed(names: tuple[str, ...], seed_norm: str) -> bool:
    """True when any endpoint name involves the beam seed entity."""
    if not seed_norm:
        return True
    for name in names:
        n = normalize_name(name)
        if n and (n == seed_norm or seed_norm in n or n in seed_norm):
            return True
    return False


def neighbor_candidates(
    ranked: list[tuple[float, RelationRecord, EntityRecord, str]],
    entity_name: str,
    preferred: list[str] | None,
) -> list[Candidate]:
    """Wrap ranked neighbor edges (score, relation, entity, content) as candidates."""
    out: list[Candidate] = []
    for i, (score, rel, ent, content) in enumerate(ranked):
        head = rel.head_name or entity_name
        tail = rel.tail_name or ent.name
        sources = source_citations([rel])
        out.append(
            Candidate(
                id=f"nbr:{entity_name}:{rel.type}:{ent.name}:{i}",
                source=CandidateSource.GRAPH_NEIGHBOR,
                content=content,
                score=score,
                structured={
                    "kind": "neighbor",
                    "query_entity": entity_name,
                    "relation": rel.type,
                    "head": head,
                    "tail": tail,
                    "neighbor": ent.name,
                    "neighbor_type": ent.type,
                    "attributes": ent.attributes,
                    "relation_attributes": rel.attributes,
                    "preferred_relations": preferred,
                    "source_chunk_ids": source_chunk_ids(sources),
                },
                citations=[
                    Citation(entity_id=ent.id, relation_id=rel.id, span=content),
                    *sources,
                ],
                metadata={"beam_rank": i},
            )
        )
    return out
