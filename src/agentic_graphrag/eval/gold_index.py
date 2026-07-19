"""Adjacency index over seed triples for gold-case generation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from agentic_graphrag.knowledge.schema_check import Triple


@dataclass
class Edge:
    head: str
    relation: str
    tail: str
    confidence: float


def index_triples(
    triples: list[Triple],
) -> tuple[list[Edge], dict[str, list[Edge]], dict[str, list[Edge]]]:
    edges: list[Edge] = []
    out_adj: dict[str, list[Edge]] = defaultdict(list)
    in_adj: dict[str, list[Edge]] = defaultdict(list)
    for t in triples:
        e = Edge(
            head=t.head.name.strip(),
            relation=t.relation.upper(),
            tail=t.tail.name.strip(),
            confidence=float(t.confidence),
        )
        if not e.head or not e.tail:
            continue
        edges.append(e)
        out_adj[e.head].append(e)
        in_adj[e.tail].append(e)
    return edges, out_adj, in_adj
