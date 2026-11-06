"""Structured-output capability negotiation without guessed vendor names."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StructuredFormatClient(Protocol):
    def format_for_schema(self, schema: dict[str, Any]) -> dict[str, Any] | None: ...


def strict_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: {name: strict_schema(child) for name, child in item.items()}
        if key in {"properties", "$defs", "definitions", "patternProperties"}
        else strict_schema(item)
        for key, item in value.items()
        if key != "default"
    }
    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def _has_dictionaries(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_dictionaries(item) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("type") == "object" and (
        value.get("additionalProperties", False) is not False
        or ("properties" not in value and "additionalProperties" not in value)
    ):
        return True
    return any(_has_dictionaries(item) for item in value.values())


def output_format(mode: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    if mode == "text":
        return None
    # Strict provider schemas require closed objects. Keep arbitrary dictionary
    # keys by using JSON mode and the caller's existing Pydantic validation.
    if mode == "json_object" or _has_dictionaries(schema):
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": strict_schema(schema)},
    }
