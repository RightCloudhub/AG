"""Answer policy for degraded retrieval runs (O-9).

A run whose channels failed must not come back looking like a normal answer.
:func:`apply_degradation_policy` copies the run's channel health onto the chain
(``metadata.degraded_channels``) and flags ``low_confidence`` when *every* channel
listed in ``config.degradation.low_confidence_channels`` reported ``failed`` — by
default the vector and graph channels, i.e. no retrieval left at all.

Only the flag is added here: ranking and answer text are untouched.
"""

from __future__ import annotations

from typing import Any

from agentic_graphrag.config import AppConfig
from agentic_graphrag.core.enums import RetrievalChannel
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.generation.entailment import JUDGE_ABSTAINED
from agentic_graphrag.observability.degradation import (
    DEGRADED_CHANNELS_KEY,
    LOW_CONFIDENCE_KEY,
    REASON_LLM_UNAVAILABLE,
    ChannelHealth,
    degraded_channel,
    merge_degraded_channels,
)

__all__ = [
    "DEGRADED_CHANNELS_KEY",
    "LOW_CONFIDENCE_KEY",
    "apply_degradation_policy",
    "promote_degradation_fields",
]

#: Set by ``generation.answer`` when it falls back to extractive answering. That
#: layer sits below the executor and has no ``ChannelHealth`` handle.
LLM_DEGRADED_KEY = "llm_degraded"


def apply_degradation_policy(chain: ReasoningChain, health: ChannelHealth, cfg: AppConfig) -> None:
    """Record channel health on the chain, flagging answers that lost both channels.

    Channel health wins over markers a deeper layer already wrote.
    """
    rows = merge_degraded_channels(
        chain.metadata.get(DEGRADED_CHANNELS_KEY),
        _deeper_markers(chain),
        health.degraded_channels(),
    )
    if not rows:
        return
    meta: dict[str, Any] = {**chain.metadata, DEGRADED_CHANNELS_KEY: rows}
    required = {str(name) for name in cfg.degradation.low_confidence_channels}
    if required and required <= health.failed_channels():
        meta[LOW_CONFIDENCE_KEY] = True
    chain.metadata = meta


def promote_degradation_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy ``metadata.degraded_channels`` / ``low_confidence`` to response fields.

    Chains carry them in metadata; every path that turns a chain into a response
    (fresh run *and* answer-cache replay) must promote them, or a degraded answer
    comes back looking clean.
    """
    meta = payload.get("metadata") or {}
    if isinstance(meta, dict):
        payload[DEGRADED_CHANNELS_KEY] = meta.get(DEGRADED_CHANNELS_KEY) or []
        payload[LOW_CONFIDENCE_KEY] = bool(meta.get(LOW_CONFIDENCE_KEY))
    return payload


def _deeper_markers(chain: ReasoningChain) -> list[dict[str, str]]:
    """Channels marked where the fallback happened, below the executor."""
    if chain.metadata.get(LLM_DEGRADED_KEY) or chain.metadata.get(JUDGE_ABSTAINED):
        return [degraded_channel(RetrievalChannel.LLM, REASON_LLM_UNAVAILABLE)]
    return []
