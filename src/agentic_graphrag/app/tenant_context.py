"""Build a query's entity vocabulary from its own graph partition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.core.ports.capabilities import EntityLister

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService

ENTITY_SCAN_LIMIT = 100_000


def tenant_entities(service: QueryService, tenant_id: str) -> list[str]:
    graph = service.bundle.graph
    if not isinstance(graph, EntityLister):
        return list(service.known_entities) if tenant_id in {"", "default"} else []
    rows = graph.list_entities(limit=ENTITY_SCAN_LIMIT, tenant_id=tenant_id or None)
    return list(dict.fromkeys(entity.name for entity in rows))
