"""Validated, cached schema-level language aliases for deterministic matching."""

from functools import lru_cache

import yaml
from pydantic import BaseModel, Field

from agentic_graphrag.config import get_config, resolve_path


class QuestionMatchingConfig(BaseModel):
    entity_similarity_threshold: float = Field(default=0.90, ge=0.8, le=1.0)
    entity_similarity_margin: float = Field(default=0.05, ge=0.0, le=1.0)
    entity_min_chars: int = Field(default=8, ge=5)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    chinese_frames: dict[str, list[str]] = Field(default_factory=dict)
    path_cues: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def question_matching_config() -> QuestionMatchingConfig:
    path = resolve_path(get_config().question_matching_path)
    return QuestionMatchingConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
