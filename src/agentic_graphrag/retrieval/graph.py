"""Graph multi-hop retrieval: candidate / citation assembly (FR-RT-02 / P2-RT-01).

Beam traversal and relation scoring live in
:mod:`agentic_graphrag.retrieval.graph_beam`. Path helpers in
:mod:`agentic_graphrag.retrieval.graph_paths`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation
from agentic_graphrag.retrieval.graph_beam import (
    BeamConfig,
    BeamExpander,
    RelationEmbedSim,
    blend_relation_score,
    edge_score,
    infer_relation_types,
    normalize_name,
    relation_relevance,
)
from agentic_graphrag.retrieval.graph_paths import path_candidates, score_paths
from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore, RelationRecord

if TYPE_CHECKING:
    from agentic_graphrag.config import AppConfig, GraphRetrievalConfig

__all__ = [
    "GraphRetriever",
    "blend_relation_score",
    "infer_relation_types",
    "relation_relevance",
]


class GraphRetriever:
    def __init__(
        self,
        store: GraphStore,
        *,
        max_neighbors_per_layer: int = 50,
        max_paths: int = 20,
        default_neighbor_hops: int = 1,
        default_path_hops: int = 4,
        beam_width: int = 20,
        high_degree_threshold: int = 30,
        relation_relevance_threshold: float = 0.12,
        relation_embed_sim: RelationEmbedSim | None = None,
    ) -> None:
        self.store = store
        self.max_neighbors_per_layer = max_neighbors_per_layer
        self.max_paths = max_paths
        self.default_neighbor_hops = default_neighbor_hops
        self.default_path_hops = default_path_hops
        self.beam_width = beam_width
        self.high_degree_threshold = high_degree_threshold
        self.relation_relevance_threshold = relation_relevance_threshold
        self.relation_embed_sim = relation_embed_sim
        self._beam = BeamExpander(
            store,
            BeamConfig(
                max_neighbors_per_layer=max_neighbors_per_layer,
                max_paths=max_paths,
                beam_width=beam_width,
                high_degree_threshold=high_degree_threshold,
                relation_relevance_threshold=relation_relevance_threshold,
                relation_embed_sim=relation_embed_sim,
            ),
        )

    @classmethod
    def from_config(
        cls,
        store: GraphStore,
        cfg: GraphRetrievalConfig | AppConfig | None = None,
    ) -> GraphRetriever:
        """Build from application / graph retrieval config (P2-RT-01 caps)."""
        from agentic_graphrag.config import AppConfig, get_config

        if cfg is None:
            g = get_config().retrieval.graph
        elif isinstance(cfg, AppConfig):
            g = cfg.retrieval.graph
        else:
            g = cfg
        return cls(
            store,
            max_neighbors_per_layer=g.max_neighbors_per_layer,
            max_paths=g.max_paths,
            default_neighbor_hops=g.max_hop_neighbors,
            default_path_hops=g.max_path_hops,
            beam_width=g.beam_width,
            high_degree_threshold=g.high_degree_threshold,
            relation_relevance_threshold=g.relation_relevance_threshold,
        )

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int | None = None,
        relation_types: list[str] | None = None,
        limit: int | None = None,
        sub_question: str | None = None,
    ) -> list[Candidate]:
        hops = max_hops if max_hops is not None else self.default_neighbor_hops
        lim = min(limit or self.max_neighbors_per_layer, self.max_neighbors_per_layer)
        preferred = relation_types or infer_relation_types(
            sub_question, min_score=self.relation_relevance_threshold
        )
        beams = self._beam.beam_expand(
            entity_name,
            max_hops=max(1, hops),
            preferred_relations=preferred,
            sub_question=sub_question,
        )
        ranked = self._rank_neighbor_edges(beams, entity_name, sub_question)
        return self._neighbor_candidates(ranked[:lim], entity_name, preferred)

    def _score_edge(self, rel: RelationRecord, sub_question: str | None) -> float:
        sim = None
        scorer = self.relation_embed_sim
        if scorer is not None:
            try:
                sim = scorer(rel.type, sub_question)
            except Exception:  # noqa: BLE001 — lexical fallback
                sim = None
        return edge_score(rel, sub_question, embed_sim=sim)

    def _rank_neighbor_edges(
        self,
        beams: list,
        entity_name: str,
        sub_question: str | None,
    ) -> list[tuple[float, RelationRecord, EntityRecord, str]]:
        best: dict[str, tuple[float, RelationRecord, EntityRecord, str, int]] = {}
        qn = normalize_name(entity_name)
        seen = 0
        for rel, ent in _iter_beam_edges(beams):
            head, tail, content = _edge_labels(rel, ent, entity_name)
            key = f"{rel.type}:{normalize_name(head)}:{normalize_name(tail)}"
            sc = self._score_edge(rel, sub_question)
            sc = _apply_seed_boost(sc, rel, ends=(head, tail), seed_norm=qn)
            prev = best.get(key)
            if prev is None:
                best[key] = (sc, rel, ent, content, seen)
                seen += 1
            elif sc > prev[0]:
                best[key] = (sc, rel, ent, content, prev[4])
        ranked = sorted(best.values(), key=lambda t: (-t[0], t[4]))
        return [(sc, rel, ent, content) for sc, rel, ent, content, _ord in ranked]

    def _neighbor_candidates(
        self,
        ranked: list[tuple[float, RelationRecord, EntityRecord, str]],
        entity_name: str,
        preferred: list[str] | None,
    ) -> list[Candidate]:
        out: list[Candidate] = []
        for i, (sc, rel, ent, content) in enumerate(ranked):
            head = rel.head_name or entity_name
            tail = rel.tail_name or ent.name
            out.append(
                Candidate(
                    id=f"nbr:{entity_name}:{rel.type}:{ent.name}:{i}",
                    source=CandidateSource.GRAPH_NEIGHBOR,
                    content=content,
                    score=sc,
                    structured={
                        "kind": "neighbor",
                        "query_entity": entity_name,
                        "relation": rel.type,
                        "head": head,
                        "tail": tail,
                        "neighbor": ent.name,
                        "neighbor_type": ent.type,
                        "attributes": ent.attributes,
                        "preferred_relations": preferred,
                    },
                    citations=[Citation(entity_id=ent.id, relation_id=rel.id, span=content)],
                    metadata={"beam_rank": i},
                )
            )
        return out

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int | None = None,
        limit: int | None = None,
        sub_question: str | None = None,
    ) -> list[Candidate]:
        hops = max_hops if max_hops is not None else self.default_path_hops
        lim = min(limit or self.max_paths, self.max_paths)
        preferred = infer_relation_types(sub_question, min_score=self.relation_relevance_threshold)
        path_rows = self.store.paths(source_name, target_name, max_hops=hops, limit=lim * 3)
        if not path_rows:
            path_rows = self._beam.beam_paths(
                source_name,
                target_name,
                max_hops=hops,
                preferred_relations=preferred,
                sub_question=sub_question,
            )
        scored = score_paths(path_rows, sub_question)
        return path_candidates(scored[:lim], source_name, target_name)

    def subgraph(
        self,
        seed_entities: list[str],
        *,
        max_hops: int | None = None,
        relation_types: list[str] | None = None,
        limit: int | None = None,
        sub_question: str | None = None,
    ) -> list[Candidate]:
        """Seed set + relation constraints → union of pruned neighbor expansions."""
        hops = max_hops if max_hops is not None else self.default_neighbor_hops
        per_seed = max(1, (limit or self.max_neighbors_per_layer) // max(len(seed_entities), 1))
        seen: set[str] = set()
        out: list[Candidate] = []
        for seed in seed_entities:
            if not seed.strip():
                continue
            group = self.neighbors(
                seed,
                max_hops=hops,
                relation_types=relation_types,
                limit=per_seed,
                sub_question=sub_question,
            )
            for c in group:
                key = c.content.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(c)
        out.sort(key=lambda c: (-c.score, c.id))
        lim = limit or self.max_neighbors_per_layer
        return out[:lim]


def _iter_beam_edges(beams: list):
    for item in beams:
        yield from item.edges


def _edge_labels(rel: RelationRecord, ent: EntityRecord, entity_name: str) -> tuple[str, str, str]:
    head = rel.head_name or entity_name
    tail = rel.tail_name or ent.name
    if not rel.head_name and not rel.tail_name:
        head, tail = entity_name, ent.name
    content = f"{head} -[{rel.type}]-> {tail} ({ent.type})"
    return head, tail, content


def _apply_seed_boost(
    score: float,
    rel: RelationRecord,
    *,
    ends: tuple[str, str],
    seed_norm: str,
) -> float:
    names = (*ends, rel.head_name or "", rel.tail_name or "")
    if _names_touch_seed(names, seed_norm):
        return score + 1.0
    return score * 0.2


def _names_touch_seed(names: tuple[str, ...], seed_norm: str) -> bool:
    """True when any endpoint name involves the beam seed entity."""
    if not seed_norm:
        return True
    for name in names:
        n = normalize_name(name)
        if n and (n == seed_norm or seed_norm in n or n in seed_norm):
            return True
    return False
