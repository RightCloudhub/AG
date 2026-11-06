"""Copy mutable record payloads without walking every immutable scalar field."""

from copy import copy, deepcopy
from typing import Any, cast

from agentic_graphrag.core.ports.stores import (
    ChunkRecord,
    DocumentRecord,
    EntityRecord,
    RelationRecord,
)

_ATOMIC = frozenset({str, int, float, bool, bytes, type(None)})


def copy_record[Record: (EntityRecord, RelationRecord, DocumentRecord, ChunkRecord)](
    record: Record, *, memo: dict[int, Any] | None = None
) -> Record:
    copies = {} if memo is None else memo
    graph_record = isinstance(record, (EntityRecord, RelationRecord))
    if graph_record and id(record) in copies:
        return cast(Record, copies[id(record)])
    result = copy(record)
    if isinstance(result, (EntityRecord, RelationRecord)):
        copies[id(record)] = result
        result.attributes = _copy_value(result.attributes, copies)
        result.sources = _copy_value(result.sources, copies)
    else:
        result.metadata = _copy_value(result.metadata, copies)
    if isinstance(result, EntityRecord):
        result.aliases = list(result.aliases)
    elif isinstance(result, DocumentRecord):
        result.chunks = [copy_record(chunk, memo=copies) for chunk in result.chunks]
    elif isinstance(result, ChunkRecord) and result.embedding is not None:
        result.embedding = list(result.embedding)
    return result


def _copy_value(value: Any, memo: dict[int, Any]) -> Any:
    kind = type(value)
    if kind in _ATOMIC:
        return value
    if id(value) in memo:
        return memo[id(value)]
    if kind is dict:
        result = value.copy()
        memo[id(value)] = result
        for key, item in value.items():
            if type(item) not in _ATOMIC:
                result[key] = _copy_value(item, memo)
        return result
    if kind is list:
        items = value.copy()
        memo[id(value)] = items
        for i, item in enumerate(items):
            if type(item) not in _ATOMIC:
                items[i] = _copy_value(item, memo)
        return items
    return deepcopy(value, memo)
