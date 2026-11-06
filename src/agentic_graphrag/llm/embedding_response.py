"""Validate ordered, finite embedding batches at the provider boundary."""

import math
from typing import Any


def embedding_rows(
    data: dict[str, Any], *, count: int, dimensions: int | None = None
) -> list[list[float]]:
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Embedding response does not match the input batch")
    ordered: dict[int, list[float]] = {}
    width = dimensions
    for row in rows:
        index = _row_index(row, count, ordered)
        vector = _finite_vector(row.get("embedding"))
        width = width or len(vector)
        if len(vector) != width:
            raise ValueError("Embedding dimensions do not match the index space")
        ordered[index] = [float(v) for v in vector]
    return [ordered[index] for index in range(count)]


def _row_index(row: Any, count: int, seen: dict[int, list[float]]) -> int:
    if not isinstance(row, dict):
        raise ValueError("Embedding response rows must be objects")
    index = row.get("index", 0 if count == 1 else None)
    if type(index) is not int or index not in range(count) or index in seen:
        raise ValueError("Embedding response indexes do not match the input batch")
    return index


def _finite_vector(vector: object) -> list[float]:
    if not isinstance(vector, list) or not vector:
        raise ValueError("Embedding vector must be a nonempty list")
    if not all(type(v) in {int, float} and math.isfinite(v) for v in vector):
        raise ValueError("Embedding vector contains non-finite or nonnumeric values")
    return [float(v) for v in vector]
