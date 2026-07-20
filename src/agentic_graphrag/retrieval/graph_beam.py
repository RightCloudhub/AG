"""Beam traversal core for graph multi-hop retrieval (FR-RT-02 / P2-RT-01).

Relation-cue scoring, edge/path signatures, and beam expansion. Candidate /
citation assembly lives in :mod:`agentic_graphrag.retrieval.graph`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore, PathRecord, RelationRecord

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
    scored = _score_relation_cues(sub_question, avail, min_score)
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [r for _, r in scored]


def _score_relation_cues(
    sub_question: str,
    avail: set[str] | None,
    min_score: float,
) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for rel in _RELATION_CUES:
        if avail is not None and rel not in avail:
            continue
        s = relation_relevance(rel, sub_question)
        if s >= min_score:
            scored.append((s, rel))
    return scored


def edge_score(rel: RelationRecord, sub_question: str | None) -> float:
    conf = float(rel.confidence) if rel.confidence is not None else 1.0
    conf = max(0.0, min(1.0, conf))
    return conf * relation_relevance(rel.type, sub_question)


def path_signature(nodes: list[EntityRecord], rels: list[RelationRecord]) -> str:
    parts: list[str] = []
    for i, n in enumerate(nodes):
        parts.append(n.name.lower())
        if i < len(rels):
            parts.append(rels[i].type.upper())
    return "|".join(parts)


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


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
            sc = edge_score(rel, sub_question)
            # Prune low-relevance when we have preferred types
            if preferred_relations and rel.type.upper() not in {
                r.upper() for r in preferred_relations
            }:
                if sc < self.cfg.relation_relevance_threshold:
                    continue
            scored.append((sc, rel, ent))

        scored.sort(key=lambda t: (-t[0], t[1].type, t[2].name))
        return scored[: self.cfg.max_neighbors_per_layer]

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
