"""Capabilities and replay identity of a pinned model endpoint."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ProviderOptions:
    auth_required: bool = True
    embedding_auth_required: bool = True
    cache_namespace: str = "default"
    structured_output: Literal["json_schema", "json_object", "text"] = "json_object"
    embedding_dimensions: int | None = None
    send_temperature: bool = True
    max_output_tokens: int | None = None
