"""Graph multi-hop retrieval with beam pruning (FR-RT-02 / P2-RT-01).

Enhancements over flat neighbor dump:
- sub-question-driven relation-type preference (lexical cues; no live embed required)
- scored beam expansion per hop (confidence × relation relevance)
- path / edge dedup + Top-K sampling by score
- layer / path / beam caps from :class:`GraphRetrievalConfig` (no magic defaults)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource, Citation
from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore, PathRecord, RelationRecord

if TYPE_CHECKING:
    from agentic_graphrag.config import AppConfig, GraphRetrievalConfig

# Lexical cues for offline relation relevance (pure logic; embedding re-rank is P3).
_RELATION_CUES: dict[str, tuple[str, ...]] = {
    "CEO_OF": ("ceo", "chief executive", "leads", "leader", "headed by"),
    "WORKS_AT": ("work", "works", "worked", "employ", "job", "role", "title"),
    "WORKED_AT": ("previously work", "worked at", "former", "prior", "previously"),
    "PARENT_OF": ("parent", "owns", "own", "holding", "subsidiary of", "parent company"),
    "SUBSIDIARY_OF": ("subsidiary", "owned by", "child company", "unit of"),
    "PRODUCES": ("produce", "product", "manufactur", "makes", "offers"),
    "COMPETES_WITH": ("compet", "rival", "versus", "vs"),
    "SUPPLIES": ("suppl", "vendor", "logistics"),
    "SUPPLIES_FOR": ("suppl", "component", "parts for"),
    "PARTICIPATED_IN": ("participat", "event", "partnership", "deal", "summit"),
    "ACQUIRED": ("acquir", "bought", "merger", "m&a"),
    "LOCATED_IN": ("located", "based in", "headquarter", "hq"),
}


def relation_relevance(relation_type: str, sub_question: str | None) -> float:
    """Score in [0, 1] how well a relation type matches the sub-question text."""
    if not sub_question:
        return 0.5
    q = sub_question.lower()
    cues = _RELATION_CUES.get(relation_type.upper(), ())
    if not cues:
        # Unknown relation: mild default so it is not totally pruned
        return 0.25
    hits = sum(1 for c in cues if c in q)
    if hits == 0:
        return 0.08
    return min(1.0, 0.45 + 0.2 * hits)


def infer_relation_types(
    sub_question: str | None,
    *,
    available: Iterable[str] | None = None,
    min_score: float = 0.2,
) -> list[str] | None:
    """Return preferred relation types for the sub-question, or None if unfiltered.

    None means "no strong cue" → caller may still score edges but not hard-filter.
    """
    if not sub_question or not sub_question.strip():
        return None
    avail = {a.upper() for a in available} if available is not None else None
    scored: list[tuple[float, str]] = []
    for rel, _cues in _RELATION_CUES.items():
        if avail is not None and rel not in avail:
            continue
        s = relation_relevance(rel, sub_question)
        if s >= min_score:
            scored.append((s, rel))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [r for _, r in scored]


def _edge_score(rel: RelationRecord, sub_question: str | None) -> float:
    conf = float(rel.confidence) if rel.confidence is not None else 1.0
    conf = max(0.0, min(1.0, conf))
    return conf * relation_relevance(rel.type, sub_question)


def _path_signature(nodes: list[EntityRecord], rels: list[RelationRecord]) -> str:
    parts: list[str] = []
    for i, n in enumerate(nodes):
        parts.append(n.name.lower())
        if i < len(rels):
            parts.append(rels[i].type.upper())
    return "|".join(parts)


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


@dataclass
class _BeamItem:
    score: float
    node_name: str
    nodes: list[EntityRecord]
    rels: list[RelationRecord]
    # Edges collected along the beam for neighbor-candidate emission
    edges: list[tuple[RelationRecord, EntityRecord]]


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

        beams = self._beam_expand(
            entity_name,
            max_hops=max(1, hops),
            preferred_relations=preferred,
            sub_question=sub_question,
        )

        # Flatten unique edges with best score; keep order by score desc
        best: dict[str, tuple[float, RelationRecord, EntityRecord, str]] = {}
        for item in beams:
            for rel, ent in item.edges:
                head = rel.head_name or entity_name
                tail = rel.tail_name or ent.name
                if not rel.head_name and not rel.tail_name:
                    head, tail = entity_name, ent.name
                key = f"{rel.type}:{_normalize_name(head)}:{_normalize_name(tail)}"
                sc = _edge_score(rel, sub_question)
                prev = best.get(key)
                if prev is None or sc > prev[0]:
                    best[key] = (sc, rel, ent, f"{head} -[{rel.type}]-> {tail} ({ent.type})")

        ranked = sorted(best.values(), key=lambda t: (-t[0], t[3]))
        out: list[Candidate] = []
        for i, (sc, rel, ent, content) in enumerate(ranked[:lim]):
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
                    citations=[
                        Citation(
                            entity_id=ent.id,
                            relation_id=rel.id,
                            span=content,
                        )
                    ],
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
        preferred = infer_relation_types(
            sub_question, min_score=self.relation_relevance_threshold
        )

        # Prefer store paths then re-score/dedup; fall back to beam join if empty
        path_rows = self.store.paths(source_name, target_name, max_hops=hops, limit=lim * 3)
        if not path_rows:
            path_rows = self._beam_paths(
                source_name,
                target_name,
                max_hops=hops,
                preferred_relations=preferred,
                sub_question=sub_question,
            )

        scored: list[tuple[float, PathRecord, str]] = []
        seen: set[str] = set()
        for path in path_rows:
            sig = _path_signature(path.nodes, path.relations)
            if sig in seen:
                continue
            seen.add(sig)
            # Path score: length-penalized mean edge score
            if path.relations:
                edge_scores = [_edge_score(r, sub_question) for r in path.relations]
                mean_e = sum(edge_scores) / len(edge_scores)
            else:
                mean_e = 0.0
            sc = mean_e / max(path.length, 1)
            # Prefer shorter paths when scores tie-ish
            sc = sc + 0.01 / max(path.length, 1)
            if path.score:
                sc = max(sc, float(path.score) * mean_e if mean_e else float(path.score))
            parts: list[str] = []
            for j, node in enumerate(path.nodes):
                parts.append(node.name)
                if j < len(path.relations):
                    parts.append(f"-[{path.relations[j].type}]->")
            content = " ".join(parts)
            scored.append((sc, path, content))

        scored.sort(key=lambda t: (-t[0], t[2]))
        out: list[Candidate] = []
        for i, (sc, path, content) in enumerate(scored[:lim]):
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
                        "signature": _path_signature(path.nodes, path.relations),
                    },
                    citations=[Citation(entity_id=n.id, span=n.name) for n in path.nodes],
                    metadata={"rank": i},
                )
            )
        return out

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
        groups: list[list[Candidate]] = []
        for seed in seed_entities:
            if not seed.strip():
                continue
            groups.append(
                self.neighbors(
                    seed,
                    max_hops=hops,
                    relation_types=relation_types,
                    limit=per_seed,
                    sub_question=sub_question,
                )
            )
        # Dedup by content signature across seeds
        seen: set[str] = set()
        out: list[Candidate] = []
        for group in groups:
            for c in group:
                key = c.content.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(c)
        out.sort(key=lambda c: (-c.score, c.id))
        lim = limit or self.max_neighbors_per_layer
        return out[:lim]

    # --- internals ---------------------------------------------------------

    def _layer_edges(
        self,
        entity_name: str,
        *,
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[tuple[float, RelationRecord, EntityRecord]]:
        """1-hop edges from store, scored and pruned."""
        fetch_limit = max(self.max_neighbors_per_layer * 2, self.beam_width * 2)
        # Soft filter: pass preferred when high-degree risk; else fetch all and score
        hard_types = preferred_relations
        rows = self.store.neighbors(
            entity_name,
            max_hops=1,
            relation_types=hard_types if self._looks_high_degree(entity_name) else None,
            limit=fetch_limit,
        )
        # If hard filter yielded nothing, retry unfiltered
        if hard_types and not rows:
            rows = self.store.neighbors(
                entity_name, max_hops=1, relation_types=None, limit=fetch_limit
            )

        scored: list[tuple[float, RelationRecord, EntityRecord]] = []
        for rel, ent in rows:
            sc = _edge_score(rel, sub_question)
            # Prune low-relevance when we have preferred types
            if preferred_relations and rel.type.upper() not in {
                r.upper() for r in preferred_relations
            }:
                if sc < self.relation_relevance_threshold:
                    continue
            scored.append((sc, rel, ent))

        scored.sort(key=lambda t: (-t[0], t[1].type, t[2].name))
        return scored[: self.max_neighbors_per_layer]

    def _looks_high_degree(self, entity_name: str) -> bool:
        rows = self.store.neighbors(
            entity_name, max_hops=1, relation_types=None, limit=self.high_degree_threshold + 1
        )
        return len(rows) > self.high_degree_threshold

    def _beam_expand(
        self,
        entity_name: str,
        *,
        max_hops: int,
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[_BeamItem]:
        start = EntityRecord(id="", name=entity_name, type="Entity")
        beams: list[_BeamItem] = [
            _BeamItem(score=1.0, node_name=entity_name, nodes=[start], rels=[], edges=[])
        ]
        all_frontier = list(beams)

        for _hop in range(max(1, max_hops)):
            nxt: list[_BeamItem] = []
            for item in beams:
                layer = self._layer_edges(
                    item.node_name,
                    preferred_relations=preferred_relations,
                    sub_question=sub_question,
                )
                for sc, rel, ent in layer:
                    # Avoid immediate cycles on node names
                    if _normalize_name(ent.name) in {
                        _normalize_name(n.name) for n in item.nodes
                    }:
                        continue
                    new_score = item.score * max(sc, 1e-6)
                    nxt.append(
                        _BeamItem(
                            score=new_score,
                            node_name=ent.name,
                            nodes=item.nodes + [ent],
                            rels=item.rels + [rel],
                            edges=item.edges + [(rel, ent)],
                        )
                    )
            if not nxt:
                break
            nxt.sort(key=lambda b: (-b.score, b.node_name))
            # Cap fan-out per layer
            beams = nxt[: self.beam_width]
            all_frontier.extend(beams)
        return all_frontier

    def _beam_paths(
        self,
        source_name: str,
        target_name: str,
        *,
        max_hops: int,
        preferred_relations: list[str] | None,
        sub_question: str | None,
    ) -> list[PathRecord]:
        target_key = _normalize_name(target_name)
        found: list[PathRecord] = []
        beams = self._beam_expand(
            source_name,
            max_hops=max_hops,
            preferred_relations=preferred_relations,
            sub_question=sub_question,
        )
        seen: set[str] = set()
        for item in beams:
            if _normalize_name(item.node_name) != target_key:
                continue
            if not item.rels:
                continue
            sig = _path_signature(item.nodes, item.rels)
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
        return found[: self.max_paths]
