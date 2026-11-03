"""Optional model selection for clients used by the requirement verifier."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from agentic_graphrag.config_register import RegisterVerifierConfig
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.llm.roles import model_name, role_client

logger = logging.getLogger(__name__)


@runtime_checkable
class VerifierFactory(Protocol):
    def for_verifier(self, *, model: str | None = None) -> LLMClient: ...


def warn_same_model(main_model: str, verifier_model: str) -> None:
    if main_model == verifier_model:
        logger.warning(
            "Requirement register verifier uses the same model as the proposer: %s",
            verifier_model,
        )


def build_verifier(llm: LLMClient, config: RegisterVerifierConfig) -> LLMClient:
    if isinstance(llm, VerifierFactory):
        return llm.for_verifier(model=config.model)
    if config.model is not None:
        raise ValueError("LLM client does not support register.verifier.model selection")
    logger.warning("Requirement register verifier model independence is unknown for this client")
    return llm


def build_role_verifier(llm: LLMClient, config: RegisterVerifierConfig) -> LLMClient:
    verifier = build_verifier(llm, config) if config.model else role_client(llm, "verifier")
    warn_same_model(model_name(role_client(llm, "researcher")), model_name(verifier))
    return verifier
