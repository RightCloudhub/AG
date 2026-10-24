"""Validated text completion responses at the external provider boundary."""

from pydantic import BaseModel, Field, TypeAdapter

from agentic_graphrag.core.json_types import JsonObject

JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CompletionMessage(BaseModel):
    content: str


class CompletionChoice(BaseModel):
    message: CompletionMessage


class CompletionUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class CompletionResponse(BaseModel):
    choices: list[CompletionChoice] = Field(min_length=1)
    usage: CompletionUsage | None = None
