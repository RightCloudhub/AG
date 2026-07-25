"""Neo4j query builders and path decoding."""

from typing import Any

from agentic_graphrag.stores.interfaces import PathRecord
from agentic_graphrag.stores.neo4j_codec import node_to_entity, rel_to_record


def neighbors_query(max_hops: int, relation_types: list[str] | None) -> str:
    if max_hops == 1:
        return one_hop_query(relation_types)
    last_rel_pred = "AND type(last(relationships(path))) IN $rel_types" if relation_types else ""
    tenant_pred = """
      AND ($tenant_id IS NULL OR ALL(edge IN relationships(path)
          WHERE coalesce(edge.tenant_id, '') IN [$tenant_id, '']))
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
    if relation_types:
        return """
        MATCH (src)-[r]-(dst)
        WHERE toLower(src.name) = toLower($name)
          AND type(r) IN $rel_types
          AND ($tenant_id IS NULL OR coalesce(r.tenant_id, '') IN [$tenant_id, ''])
          AND src <> dst
        RETURN dst, r, 1 AS hops
        ORDER BY coalesce(r.id, ''), dst.name
        LIMIT $limit
        """
    return """
    MATCH (src)-[r]-(dst)
    WHERE toLower(src.name) = toLower($name) AND src <> dst
      AND ($tenant_id IS NULL OR coalesce(r.tenant_id, '') IN [$tenant_id, ''])
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
