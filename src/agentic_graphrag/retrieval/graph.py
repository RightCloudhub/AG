"""Graph multi-hop retrieval: candidate / citation assembly (FR-RT-02 / P2-RT-01).

Beam traversal and relation scoring live in
:mod:`agentic_graphrag.retrieval.graph_beam`; path helpers in
:mod:`agentic_graphrag.retrieval.graph_paths`; neighbor-candidate assembly and
edge labels in :mod:`agentic_graphrag.retrieval.graph_candidates`.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore, RelationRecord
from agentic_graphrag.observability.degradation import (
    REASON_GRAPH_STORE_UNAVAILABLE,
    ChannelHealth,
    ChannelStatus,
    degradation_guard,
)
from agentic_graphrag.retrieval.graph_beam import (
    BeamConfig,
    BeamExpander,
    BeamItem,
    RelationEmbedSim,
    blend_relation_score,
    edge_score,
    infer_relation_types,
    normalize_name,
    relation_relevance,
)
from agentic_graphrag.retrieval.graph_candidates import (
    apply_seed_boost,
    edge_labels,
    iter_beam_edges,
    neighbor_candidates,
)
from agentic_graphrag.retrieval.graph_paths import path_candidates, score_paths

if TYPE_CHECKING:
    from agentic_graphrag.config import AppConfig, GraphRetrievalConfig

__all__ = [
    "GraphRetriever",
    "blend_relation_score",
    "infer_relation_types",
    "relation_relevance",
]


def _graph_store_guard(health: ChannelHealth | None) -> AbstractContextManager[None]:
    """Guard for direct store reads: an unreachable graph store fails the channel (O-9)."""
    return degradation_guard(
        RetrievalChannel.GRAPH,
        REASON_GRAPH_STORE_UNAVAILABLE,
        severity=ChannelStatus.FAILED,
        health=health,
    )


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
        health: ChannelHealth | None = None,
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
        self.health = health
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
            health=health,
        )

    @classmethod
    def from_config(
        cls,
        store: GraphStore,
        cfg: GraphRetrievalConfig | AppConfig | None = None,
        *,
        health: ChannelHealth | None = None,
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
            health=health,
        )

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int | None = None,
        relation_types: list[str] | None = None,
        limit: int | None = None,
        sub_question: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Candidate]:
        hops = max_hops if max_hops is not None else self.default_neighbor_hops
        lim = min(limit or self.max_neighbors_per_layer, self.max_neighbors_per_layer)
        preferred = relation_types or infer_relation_types(
            sub_question, min_score=self.relation_relevance_threshold
        )
        with _graph_store_guard(self.health):
            beams = self._beam.beam_expand(
                entity_name,
                max_hops=max(1, hops),
                preferred_relations=preferred,
                sub_question=sub_question,
                tenant_id=tenant_id,
            )
        ranked = self._rank_neighbor_edges(beams, entity_name, sub_question)
        return neighbor_candidates(ranked[:lim], entity_name, preferred)

    def _score_edge(self, rel: RelationRecord, sub_question: str | None) -> float:
        # The beam owns the guarded relation-embedding call, so an embedder outage
        # is recorded once per run instead of once per edge (O-9).
        sim = self._beam.relation_sim(rel.type, sub_question)
        return edge_score(rel, sub_question, embed_sim=sim)

    def _rank_neighbor_edges(
        self,
        beams: list[BeamItem],
        entity_name: str,
        sub_question: str | None,
    ) -> list[tuple[float, RelationRecord, EntityRecord, str]]:
        best: dict[str, tuple[float, RelationRecord, EntityRecord, str, int]] = {}
        qn = normalize_name(entity_name)
        seen = 0
        for rel, ent in iter_beam_edges(beams):
            head, tail, content = edge_labels(rel, ent, entity_name)
            key = f"{rel.type}:{normalize_name(head)}:{normalize_name(tail)}"
            sc = self._score_edge(rel, sub_question)
            sc = apply_seed_boost(sc, rel, ends=(head, tail), seed_norm=qn)
            prev = best.get(key)
            if prev is None:
                best[key] = (sc, rel, ent, content, seen)
                seen += 1
            elif sc > prev[0]:
                best[key] = (sc, rel, ent, content, prev[4])
        ranked = sorted(best.values(), key=lambda t: (-t[0], t[4]))
        return [(sc, rel, ent, content) for sc, rel, ent, content, _ord in ranked]

    def paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int | None = None,
        limit: int | None = None,
        sub_question: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Candidate]:
        hops = max_hops if max_hops is not None else self.default_path_hops
        lim = min(limit or self.max_paths, self.max_paths)
        preferred = infer_relation_types(sub_question, min_score=self.relation_relevance_threshold)
        with _graph_store_guard(self.health):
            path_rows = self.store.paths(
                source_name, target_name, max_hops=hops, limit=lim * 3, tenant_id=tenant_id
            )
            if not path_rows:
                path_rows = self._beam.beam_paths(
                    source_name,
                    target_name,
                    max_hops=hops,
                    preferred_relations=preferred,
                    sub_question=sub_question,
                    tenant_id=tenant_id,
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
        tenant_id: str | None = None,
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
                tenant_id=tenant_id,
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
