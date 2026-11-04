"""Conservative public-schema compatibility checks used by the export gate."""

from typing import Any

_DOCUMENTATION = frozenset({"title", "description", "examples", "example", "$id"})
_STABLE_VALUES = frozenset({"type", "$ref", "const", "default", "pattern", "format"})
_BOUNDS = frozenset({"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"})


def breaking_changes(before: Any, after: Any, path: str = "$") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return _compare_objects(before, after, path)
    if isinstance(before, list) and before != after:
        return [f"{path}: existing schema alternatives changed"]
    if type(before) is not type(after):
        return [f"{path}: schema value type changed"]
    return []


def _compare_objects(before: dict, after: dict, path: str) -> list[str]:
    errors = []
    for key, value in before.items():
        if key in _DOCUMENTATION or key.startswith("x-"):
            continue
        location = f"{path}/{key}"
        if key not in after:
            errors.append(f"{location}: published schema member removed")
        elif key in _STABLE_VALUES | _BOUNDS and value != after[key]:
            errors.append(f"{location}: published constraint changed")
        elif key in {"required", "enum"}:
            if set(value) != set(after[key]):
                errors.append(f"{location}: required fields or enum values changed")
        else:
            errors.extend(breaking_changes(value, after[key], location))
    for key in (_BOUNDS | {"required", "enum", "additionalProperties"}) - before.keys():
        if key in after and after[key] not in ([], True):
            errors.append(f"{path}/{key}: new input constraint requires compatibility review")
    return errors
