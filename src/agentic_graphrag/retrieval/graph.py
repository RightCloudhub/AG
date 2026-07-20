"""Graph multi-hop retrieval: candidate / citation assembly (FR-RT-02 / P2-RT-01).

Beam traversal and relation scoring live in
:mod:`agentic_graphrag.retrieval.graph_beam`. This module assembles
:class:`~agentic_graphrag.retrieval.contracts.Candidate` rows with citations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation
from agentic_graphrag.retrieval.graph_beam import (
    BeamConfig,
    BeamExpander,
    edge_score,
    infer_relation_types,
    normalize_name,
    path_signature,
    relation_relevance,
)
from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore, PathRecord, RelationRecord

if TYPE_CHECKING:
    from agentic_graphrag.config import AppConfig, GraphRetrievalConfig

__all__ = [
    "GraphRetriever",
    "infer_relation_types",
    "relation_relevance",
]

_PATH_LENGTH_BONUS = 0.01


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
    ) -> None:
        self.store = store
        self.max_neighbors_per_layer = max_neighbors_per_layer
        self.max_paths = max_paths
        self.default_neighbor_hops = default_neighbor_hops
        self.default_path_hops = default_path_hops
        self.beam_width = beam_width
        self.high_degree_threshold = high_degree_threshold
        self.relation_relevance_threshold = relation_relevance_threshold
        self._beam = BeamExpander(
            store,
            BeamConfig(
                max_neighbors_per_layer=max_neighbors_per_layer,
                max_paths=max_paths,
                beam_width=beam_width,
                high_degree_threshold=high_degree_threshold,
                relation_relevance_threshold=relation_relevance_threshold,
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

    def _rank_neighbor_edges(
        self,
        beams: list,
        entity_name: str,
        sub_question: str | None,
    ) -> list[tuple[float, RelationRecord, EntityRecord, str]]:
        best: dict[str, tuple[float, RelationRecord, EntityRecord, str]] = {}
        qn = normalize_name(entity_name)
        for item in beams:
            for rel, ent in item.edges:
                head, tail, content = _edge_labels(rel, ent, entity_name)
                key = f"{rel.type}:{normalize_name(head)}:{normalize_name(tail)}"
                sc = edge_score(rel, sub_question)
                # Multi-hop beams surface edges about *other* nodes (e.g. CEO of a
                # supplier). Strongly prefer edges that touch the seed entity so
                # "CEO of BrightLink" is not answered with "CEO of NovaTech".
                if _edge_touches_seed(rel, head, tail, qn):
                    sc += 1.0
                else:
                    sc *= 0.2
                prev = best.get(key)
                if prev is None or sc > prev[0]:
                    best[key] = (sc, rel, ent, content)
        return sorted(best.values(), key=lambda t: (-t[0], t[3]))

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
        scored = _score_paths(path_rows, sub_question)
        return _path_candidates(scored[:lim], source_name, target_name)

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


def _edge_labels(
    rel: RelationRecord, ent: EntityRecord, entity_name: str
) -> tuple[str, str, str]:
    head = rel.head_name or entity_name
    tail = rel.tail_name or ent.name
    if not rel.head_name and not rel.tail_name:
        head, tail = entity_name, ent.name
    content = f"{head} -[{rel.type}]-> {tail} ({ent.type})"
    return head, tail, content


def _edge_touches_seed(
    rel: RelationRecord, head: str, tail: str, seed_norm: str
) -> bool:
    """True when the edge endpoints involve the beam seed entity."""
    if not seed_norm:
        return True
    for name in (head, tail, rel.head_name or "", rel.tail_name or ""):
        n = normalize_name(name)
        if n and (n == seed_norm or seed_norm in n or n in seed_norm):
            return True
    return False


def _score_paths(
    path_rows: list[PathRecord], sub_question: str | None
) -> list[tuple[float, PathRecord, str]]:
    scored: list[tuple[float, PathRecord, str]] = []
    seen: set[str] = set()
    for path in path_rows:
        sig = path_signature(path.nodes, path.relations)
        if sig in seen:
            continue
        seen.add(sig)
        sc = _path_score(path, sub_question)
        content = _path_content(path)
        scored.append((sc, path, content))
    scored.sort(key=lambda t: (-t[0], t[2]))
    return scored


def _path_score(path: PathRecord, sub_question: str | None) -> float:
    if path.relations:
        edge_scores = [edge_score(r, sub_question) for r in path.relations]
        mean_e = sum(edge_scores) / len(edge_scores)
    else:
        mean_e = 0.0
    sc = mean_e / max(path.length, 1)
    sc = sc + _PATH_LENGTH_BONUS / max(path.length, 1)
    if path.score:
        sc = max(sc, float(path.score) * mean_e if mean_e else float(path.score))
    return sc


def _path_content(path: PathRecord) -> str:
    parts: list[str] = []
    for j, node in enumerate(path.nodes):
        parts.append(node.name)
        if j < len(path.relations):
            parts.append(f"-[{path.relations[j].type}]->")
    return " ".join(parts)


def _path_candidates(
    scored: list[tuple[float, PathRecord, str]],
    source_name: str,
    target_name: str,
) -> list[Candidate]:
    out: list[Candidate] = []
    for i, (sc, path, content) in enumerate(scored):
        out.append(
            Candidate(
                id=f"path:{source_name}:{target_name}:{i}",
                source=CandidateSource.GRAPH_PATH,
                content=content,
                score=sc,
                structured={
                    "kind": "path",
                    "nodes": [n.name for n in path.nodes],
                    "relations": [r.type for r in path.relations],
                    "length": path.length,
                    "signature": path_signature(path.nodes, path.relations),
                },
                citations=[Citation(entity_id=n.id, span=n.name) for n in path.nodes],
                metadata={"rank": i},
            )
        )
    return out
