"""Drop merged text when its supporting source set shrinks."""

from dataclasses import replace
from typing import Any

from agentic_graphrag.core.ports.stores import EntityRecord, RelationRecord


def with_sources[T: (EntityRecord, RelationRecord)](record: T, sources: list[dict[str, Any]]) -> T:
    attributes = dict(record.attributes)
    if len(sources) != len(record.sources):
        attributes.pop("description", None)
    result = replace(record, sources=sources, attributes=attributes)
    if isinstance(result, EntityRecord) and len(sources) != len(record.sources):
        result.aliases = []
    return result
