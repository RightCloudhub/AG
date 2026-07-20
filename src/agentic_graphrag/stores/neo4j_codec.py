"""Neo4j property encode/decode helpers (nested maps → JSON strings)."""

from __future__ import annotations

import json
from typing import Any

from agentic_graphrag.stores.interfaces import EntityRecord, RelationRecord


def attrs_for_neo4j(attributes: dict[str, Any] | None) -> str:
    """Serialize attributes as JSON string (Neo4j forbids nested Map properties)."""
    return json.dumps(attributes or {}, ensure_ascii=False, sort_keys=True)


def attrs_from_neo4j(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return _parse_dict_json(raw)
    return {}


def sources_for_neo4j(sources: list[dict[str, Any]] | None) -> str:
    """Serialize sources list as JSON string (same nested-map constraint)."""
    return json.dumps(list(sources or []), ensure_ascii=False, sort_keys=True)


def sources_from_neo4j(raw: Any) -> list[dict[str, Any]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, str):
        return _parse_list_of_dicts(raw)
    return []


def _parse_dict_json(raw: str) -> dict[str, Any]:
    try:
        val = json.loads(raw)
        return dict(val) if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def _parse_list_of_dicts(raw: str) -> list[dict[str, Any]]:
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [dict(x) for x in val if isinstance(x, dict)]
    except json.JSONDecodeError:
        return []
    return []


def node_to_entity(node: Any) -> EntityRecord:
    labels = list(node.labels) if hasattr(node, "labels") else []
    etype = labels[0] if labels else "Entity"
    props = dict(node)
    return EntityRecord(
        id=str(props.get("id", props.get("name", ""))),
        name=str(props.get("name", "")),
        type=etype,
        attributes=attrs_from_neo4j(props.get("attributes")),
        aliases=list(props.get("aliases") or []),
    )


def resolve_endpoints(
    props: dict[str, Any],
    *,
    walk_from_name: str,
    walk_other_name: str,
    head_name: str,
    tail_name: str,
) -> tuple[str, str]:
    stored_head = str(props.get("head_name") or "").strip()
    stored_tail = str(props.get("tail_name") or "").strip()
    if stored_head or stored_tail:
        return _prefer(stored_head, head_name, walk_from_name), _prefer(
            stored_tail, tail_name, walk_other_name
        )
    if head_name or tail_name:
        return head_name, tail_name
    return walk_from_name, walk_other_name


def _prefer(primary: str, secondary: str, tertiary: str) -> str:
    return primary or secondary or tertiary


def rel_to_record(
    rel: Any,
    *,
    walk_from_name: str = "",
    walk_other_name: str = "",
    head_name: str = "",
    tail_name: str = "",
) -> RelationRecord:
    """Build RelationRecord with true head/tail when properties exist.

    Legacy edges without ``head_name``/``tail_name`` fall back to walk
    orientation (``walk_from`` → ``walk_other``) or explicit head/tail args.
    """
    props = dict(rel)
    h_name, t_name = resolve_endpoints(
        props,
        walk_from_name=walk_from_name,
        walk_other_name=walk_other_name,
        head_name=head_name,
        tail_name=tail_name,
    )
    return RelationRecord(
        id=str(props.get("id", f"{rel.type}:{h_name}->{t_name}")),
        type=rel.type,
        head_id=str(props.get("head_id", "")),
        tail_id=str(props.get("tail_id", "")),
        head_name=h_name,
        tail_name=t_name,
        confidence=float(props.get("confidence", 1.0)),
        attributes=attrs_from_neo4j(props.get("attributes")),
        sources=sources_from_neo4j(props.get("sources")),
    )
