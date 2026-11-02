"""Degradation / answer-policy configuration (O-9).

Split out of ``config.py`` so that module keeps headroom under the 300-line gate,
the same reason ``config_llm`` / ``config_paths`` / ``config_register`` exist.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentic_graphrag.core.enums import RetrievalChannel


class DegradationConfig(BaseModel):
    """When a degraded run must stop looking like a confident answer.

    An answer is flagged ``low_confidence`` when *every* channel listed here
    reported ``failed`` for the run — by default the two channels that carry
    multi-hop answers, so "vector down and graph down" degrades the answer while
    "re-ranker down" (the fused order still stands) does not.
    """

    low_confidence_channels: list[RetrievalChannel] = Field(
        default_factory=lambda: [RetrievalChannel.VECTOR, RetrievalChannel.GRAPH]
    )
