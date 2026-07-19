"""Graph multi-hop retrieval tools (FR-RT-02)."""

from __future__ import annotations

from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation
from agentic_graphrag.stores.interfaces import GraphStore


class GraphRetriever:
    def __init__(
        self,
        store: GraphStore,
        *,
        max_neighbors_per_layer: int = 50,
        max_paths: int = 20,
        default_neighbor_hops: int = 1,
        default_path_hops: int = 4,
    ) -> None:
        self.store = store
        self.max_neighbors_per_layer = max_neighbors_per_layer
        self.max_paths = max_paths
        self.default_neighbor_hops = default_neighbor_hops
        self.default_path_hops = default_path_hops

    def neighbors(
        self,
        entity_name: str,
        *,
        max_hops: int | None = None,
        relation_types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Candidate]:
        hops = max_hops or self.default_neighbor_hops
        lim = min(limit or self.max_neighbors_per_layer, self.max_neighbors_per_layer)
        rows = self.store.neighbors(
            entity_name,
            max_hops=hops,
            relation_types=relation_types,
            limit=lim,
        )
        out: list[Candidate] = []
        for i, (rel, ent) in enumerate(rows):
            # Prefer canonical head -[REL]-> tail when names are known
            head = rel.head_name or entity_name
            tail = rel.tail_name or ent.name
            if not rel.head_name and not rel.tail_name:
                head, tail = entity_name, ent.name
            content = f"{head} -[{rel.type}]-> {tail} ({ent.type})"
            out.append(
                Candidate(
                    id=f"nbr:{entity_name}:{rel.type}:{ent.name}:{i}",
                    source=CandidateSource.GRAPH_NEIGHBOR,
                    content=content,
                    score=rel.confidence,
                    structured={
                        "kind": "neighbor",
                        "query_entity": entity_name,
                        "relation": rel.type,
                        "head": head,
                        "tail": tail,
                        "neighbor": ent.name,
                        "neighbor_type": ent.type,
                        "attributes": ent.attributes,
                    },
                    citations=[
                        Citation(
                            entity_id=ent.id,
                            relation_id=rel.id,
                            span=content,
                        )
                    ],
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
    ) -> list[Candidate]:
        hops = max_hops or self.default_path_hops
        lim = min(limit or self.max_paths, self.max_paths)
        path_rows = self.store.paths(source_name, target_name, max_hops=hops, limit=lim)
        out: list[Candidate] = []
        for i, path in enumerate(path_rows):
            parts: list[str] = []
            for j, node in enumerate(path.nodes):
                parts.append(node.name)
                if j < len(path.relations):
                    parts.append(f"-[{path.relations[j].type}]->")
            content = " ".join(parts)
            out.append(
                Candidate(
                    id=f"path:{source_name}:{target_name}:{i}",
                    source=CandidateSource.GRAPH_PATH,
                    content=content,
                    score=path.score,
                    structured={
                        "kind": "path",
                        "nodes": [n.name for n in path.nodes],
                        "relations": [r.type for r in path.relations],
                        "length": path.length,
                    },
                    citations=[Citation(entity_id=n.id, span=n.name) for n in path.nodes],
                )
            )
        return out
