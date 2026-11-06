"""Validated text completion responses at the external provider boundary."""

from pydantic import BaseModel, Field, TypeAdapter

from agentic_graphrag.core.json_types import JsonObject

JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CompletionMessage(BaseModel):
    content: str | None = None
    refusal: str | None = None


class CompletionChoice(BaseModel):
    message: CompletionMessage
    finish_reason: str | None = None


class CompletionUsage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class CompletionResponse(BaseModel):
    choices: list[CompletionChoice] = Field(min_length=1)
    usage: CompletionUsage | None = None


class CompletionRefused(RuntimeError):
    """Provider refused the request; structure repair must not retry it."""


class CompletionTruncated(RuntimeError):
    """Output budget was exhausted; parsing repair is not a valid retry policy."""


class EmptyCompletion(RuntimeError):
    """No usable output was returned."""


def completion_text(response: CompletionResponse) -> str:
    choice = response.choices[0]
    if choice.message.refusal or choice.finish_reason == "content_filter":
        raise CompletionRefused("Provider declined the request")
    if choice.finish_reason == "length":
        raise CompletionTruncated("Provider response reached its output limit")
    if not choice.message.content or not choice.message.content.strip():
        raise EmptyCompletion("Provider returned no text")
    return choice.message.content
