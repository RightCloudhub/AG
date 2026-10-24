"""Closed configuration vocabularies fail early on invalid external values."""

import pytest
from pydantic import ValidationError

from agentic_graphrag.config import AppConfig
from agentic_graphrag.core.enums import CriticMode, FusionMethod, RerankKind
from agentic_graphrag.retrieval.fusion import fuse_candidates


@pytest.mark.parametrize(
    "section",
    [
        {"guardrails": {"critic_mode": "typo"}},
        {"retrieval": {"fusion_method": "typo"}},
        {"retrieval": {"rerank": {"kind": "typo"}}},
    ],
)
def test_invalid_mode_is_rejected_at_config_boundary(section) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(section)


def test_configuration_returns_enum_members() -> None:
    config = AppConfig.model_validate(
        {"guardrails": {"critic_mode": "tiered"}, "retrieval": {"fusion_method": "concat"}}
    )
    assert config.guardrails.critic_mode is CriticMode.TIERED
    assert config.retrieval.fusion_method is FusionMethod.CONCAT
    assert config.retrieval.rerank.kind is RerankKind.NONE


def test_invalid_fusion_method_is_rejected_even_without_candidates() -> None:
    with pytest.raises(ValueError):
        fuse_candidates(method="typo")
