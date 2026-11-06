"""Neo4j query builders and path decoding."""

from typing import Any

from agentic_graphrag.core.ports.stores import PathRecord
from agentic_graphrag.stores.neo4j_codec import node_to_entity, rel_to_record


def tenant_predicate(alias: str) -> str:
    """Only internal Cypher aliases are interpolated; the tenant is a parameter."""
    return (
        f"($tenant_id IS NULL OR coalesce({alias}.tenant_id, '') = $tenant_id "
        f"OR ($tenant_id IN ['default', ''] AND coalesce({alias}.tenant_id, '') = ''))"
    )


def neighbors_query(max_hops: int, relation_types: list[str] | None) -> str:
    if max_hops == 1:
        return one_hop_query(relation_types)
    last_rel_pred = (
        "AND coalesce(last(relationships(path)).predicate, "
        "type(last(relationships(path)))) IN $rel_types"
        if relation_types
        else ""
    )
    tenant_pred = f"""
      AND ALL(edge IN relationships(path) WHERE {tenant_predicate("edge")})
      AND ALL(node IN nodes(path) WHERE {tenant_predicate("node")})
    """
    return f"""
    MATCH (src)
    WHERE toLower(src.name) = toLower($name)
    MATCH path = (src)-[*1..{max_hops}]-(dst)
    WHERE src <> dst
      AND ALL(n IN nodes(path) WHERE size([m IN nodes(path) WHERE id(m) = id(n)]) = 1)
      {last_rel_pred}
      {tenant_pred}
    WITH dst, last(relationships(path)) AS r, length(path) AS hops
    WITH r, dst, min(hops) AS hops
    ORDER BY hops ASC, coalesce(r.id, '') ASC, dst.name ASC
    RETURN dst, r, hops
    LIMIT $limit
    """


def one_hop_query(relation_types: list[str] | None) -> str:
    relation_pred = "AND coalesce(r.predicate, type(r)) IN $rel_types" if relation_types else ""
    return f"""
    MATCH (src)-[r]-(dst)
    WHERE toLower(src.name) = toLower($name) AND src <> dst
      {relation_pred}
      AND {tenant_predicate("r")}
      AND {tenant_predicate("src")}
      AND {tenant_predicate("dst")}
    RETURN dst, r, 1 AS hops
    ORDER BY coalesce(r.id, ''), dst.name
    LIMIT $limit
    """


def path_record(path: Any) -> PathRecord:
    nodes = [node_to_entity(node) for node in path.nodes]
    relations = [
        rel_to_record(
            relation,
            walk_from_name=nodes[index].name if index < len(nodes) else "",
            walk_other_name=nodes[index + 1].name if index + 1 < len(nodes) else "",
        )
        for index, relation in enumerate(path.relationships)
    ]
    return PathRecord(
        nodes=nodes,
        relations=relations,
        length=len(relations),
        score=1.0 / max(len(relations), 1),
    )
