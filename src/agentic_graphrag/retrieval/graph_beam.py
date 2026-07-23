"""Beam traversal for graph multi-hop retrieval (FR-RT-02 / P2-RT-01)."""

from __future__ import annotations

from dataclasses import dataclass

from agentic_graphrag.retrieval.graph_relation import (
    RelationEmbedSim,
    blend_relation_score,
    edge_score,
    infer_relation_types,
    normalize_name,
    path_signature,
    relation_relevance,
)
from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore, PathRecord, RelationRecord

__all__ = [
    "BeamConfig",
    "BeamExpander",
    "BeamItem",
    "RelationEmbedSim",
    "blend_relation_score",
    "edge_score",
    "infer_relation_types",
    "normalize_name",
    "path_signature",
    "relation_relevance",
]


@dataclass
class BeamItem:
    score: float
    node_name: str
    nodes: list[EntityRecord]
    rels: list[RelationRecord]
    # Edges collected along the beam for neighbor-candidate emission
    edges: list[tuple[RelationRecord, EntityRecord]]


@dataclass(frozen=True)
class BeamConfig:
    """Caps controlling layer fan-out and high-degree hard filters."""

    max_neighbors_per_layer: int = 50
    max_paths: int = 20
    beam_width: int = 20
    high_degree_threshold: int = 30
    relation_relevance_threshold: float = 0.12
    relation_embed_sim: RelationEmbedSim | None = None


class BeamExpander:
    """Store-backed beam expansion and path join (no Candidate assembly)."""

    def __init__(self, store: GraphStore, cfg: BeamConfig) -> None:
        self.store = store
        self.cfg = cfg

    def layer_edges(
        self,
        entity_name: str,
        *,
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[tuple[float, RelationRecord, EntityRecord]]:
        """1-hop edges from store, scored and pruned."""
        fetch_limit = max(self.cfg.max_neighbors_per_layer * 2, self.cfg.beam_width * 2)
        # Soft filter: pass preferred when high-degree risk; else fetch all and score
        hard_types = preferred_relations
        rows = self.store.neighbors(
            entity_name,
            max_hops=1,
            relation_types=hard_types if self.looks_high_degree(entity_name) else None,
            limit=fetch_limit,
        )
        # If hard filter yielded nothing, retry unfiltered
        if hard_types and not rows:
            rows = self.store.neighbors(
                entity_name, max_hops=1, relation_types=None, limit=fetch_limit
            )

        scored: list[tuple[float, RelationRecord, EntityRecord]] = []
        for rel, ent in rows:
            sc = edge_score(rel, sub_question, embed_sim=self._embed_sim(rel.type, sub_question))
            # Prune low-relevance when we have preferred types
            if preferred_relations and rel.type.upper() not in {
                r.upper() for r in preferred_relations
            }:
                if sc < self.cfg.relation_relevance_threshold:
                    continue
            scored.append((sc, rel, ent))

        scored.sort(key=lambda t: (-t[0], t[1].type, t[2].name))
        return scored[: self.cfg.max_neighbors_per_layer]

    def _embed_sim(self, relation_type: str, sub_question: str | None) -> float | None:
        scorer = self.cfg.relation_embed_sim
        if scorer is None:
            return None
        try:
            return scorer(relation_type, sub_question)
        except Exception:  # noqa: BLE001 — never break retrieval on embed failure
            return None

    def looks_high_degree(self, entity_name: str) -> bool:
        rows = self.store.neighbors(
            entity_name,
            max_hops=1,
            relation_types=None,
            limit=self.cfg.high_degree_threshold + 1,
        )
        return len(rows) > self.cfg.high_degree_threshold

    def beam_expand(
        self,
        entity_name: str,
        *,
        max_hops: int,
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[BeamItem]:
        start = EntityRecord(id="", name=entity_name, type="Entity")
        beams: list[BeamItem] = [
            BeamItem(score=1.0, node_name=entity_name, nodes=[start], rels=[], edges=[])
        ]
        all_frontier = list(beams)
        for _hop in range(max(1, max_hops)):
            nxt = self._expand_layer(beams, preferred_relations, sub_question)
            if not nxt:
                break
            nxt.sort(key=lambda b: (-b.score, b.node_name))
            beams = nxt[: self.cfg.beam_width]
            all_frontier.extend(beams)
        return all_frontier

    def _expand_layer(
        self,
        beams: list[BeamItem],
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[BeamItem]:
        nxt: list[BeamItem] = []
        for item in beams:
            nxt.extend(self._extend_item(item, preferred_relations, sub_question))
        return nxt

    def _extend_item(
        self,
        item: BeamItem,
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[BeamItem]:
        seen = {normalize_name(n.name) for n in item.nodes}
        out: list[BeamItem] = []
        layer = self.layer_edges(
            item.node_name,
            preferred_relations=preferred_relations,
            sub_question=sub_question,
        )
        for sc, rel, ent in layer:
            if normalize_name(ent.name) in seen:
                continue
            out.append(
                BeamItem(
                    score=item.score * max(sc, 1e-6),
                    node_name=ent.name,
                    nodes=item.nodes + [ent],
                    rels=item.rels + [rel],
                    edges=item.edges + [(rel, ent)],
                )
            )
        return out

    def beam_paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int,
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[PathRecord]:
        target_key = normalize_name(target_name)
        found: list[PathRecord] = []
        beams = self.beam_expand(
            source_name,
            max_hops=max_hops,
            preferred_relations=preferred_relations,
            sub_question=sub_question,
        )
        seen: set[str] = set()
        for item in beams:
            if normalize_name(item.node_name) != target_key:
                continue
            if not item.rels:
                continue
            sig = path_signature(item.nodes, item.rels)
            if sig in seen:
                continue
            seen.add(sig)
            found.append(
                PathRecord(
                    nodes=item.nodes,
                    relations=item.rels,
                    length=len(item.rels),
                    score=item.score,
                )
            )
        found.sort(key=lambda p: (-p.score, p.length))
        return found[: self.cfg.max_paths]
