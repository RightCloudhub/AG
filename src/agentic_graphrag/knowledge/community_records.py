"""Stable community identity and source-dependent report fingerprints."""

from agentic_graphrag.core.communities import (
    Community,
    GraphProjection,
    ProjectedEdge,
    ProjectedNode,
)
from agentic_graphrag.knowledge.community_projection import fingerprint


def community_for_members(projection: GraphProjection, members: set[str]) -> Community:
    nodes = tuple(node for node in projection.nodes if node.id in members)
    edges = tuple(edge for edge in projection.edges if {edge.source, edge.target} <= members)
    if {node.id for node in nodes} != members:
        raise ValueError("Community membership contains an unknown entity")
    entity_ids = tuple(sorted(members))
    topology = fingerprint(
        [entity_ids, [(edge.source, edge.target, edge.weight) for edge in edges]]
    )
    content = fingerprint(
        [
            [(node.id, node.content_fingerprint) for node in nodes],
            [(edge.source, edge.target, edge.content_fingerprint) for edge in edges],
        ]
    )
    return Community(
        id="community-" + fingerprint([projection.scope.model_dump(), entity_ids])[:24],
        scope=projection.scope,
        generation=projection.generation,
        entity_ids=entity_ids,
        document_ids=_document_ids(nodes, edges),
        topology_fingerprint=topology,
        content_fingerprint=content,
        input_fingerprint=fingerprint([topology, content]),
    )


def _document_ids(
    nodes: tuple[ProjectedNode, ...], edges: tuple[ProjectedEdge, ...]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {doc for node in nodes for doc in node.document_ids}
            | {doc for edge in edges for doc in edge.document_ids}
        )
    )


def component_members(projection: GraphProjection) -> tuple[frozenset[str], ...]:
    adjacency: dict[str, set[str]] = {node.id: set() for node in projection.nodes}
    for edge in projection.edges:
        adjacency[edge.source].add(edge.target)
        adjacency[edge.target].add(edge.source)
    unseen = set(adjacency)
    result = []
    while unseen:
        seed = min(unseen)
        frontier, members = {seed}, set()
        while frontier:
            members.update(frontier)
            frontier = {other for node in frontier for other in adjacency[node]} - members
        unseen.difference_update(members)
        result.append(frozenset(members))
    return tuple(result)


def subprojection(projection: GraphProjection, members: set[str]) -> GraphProjection:
    return projection.model_copy(
        update={
            "nodes": tuple(node for node in projection.nodes if node.id in members),
            "edges": tuple(
                edge for edge in projection.edges if {edge.source, edge.target} <= members
            ),
        }
    )
