"""Tiered critic (ARCH_OPTIMIZATION_V2 O-7): LLM only when the deterministic gate is unsure."""

from __future__ import annotations

import json

import pytest

from agentic_graphrag.agent.critic import CriticAction, critique
from agentic_graphrag.agent.critic_tiered import normalize_mode
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.config import AppConfig
from agentic_graphrag.core.evidence import Candidate, CandidateSource


class CountingLLM:
    """Minimal LLMProvider stand-in that always answers NEXT_HOP."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, **_kw) -> str:
        self.calls += 1
        return json.dumps({"action": "next_hop", "rationale": "llm says more"})


def _graph_hit() -> Candidate:
    return Candidate(
        id="n1",
        source=CandidateSource.GRAPH_NEIGHBOR,
        content="Elena Varga -[CEO_OF]-> Apex Holdings (Person)",
        score=1.0,
        structured={
            "kind": "neighbor",
            "query_entity": "Apex Holdings",
            "relation": "CEO_OF",
            "head": "Elena Varga",
            "tail": "Apex Holdings",
            "neighbor": "Elena Varga",
            "neighbor_type": "Person",
        },
    )


def _ctx(mode: str, evidence: list[Candidate]) -> CritiqueContext:
    q = "Who is the CEO of Apex Holdings?"
    return CritiqueContext(
        question=q, sub_question=q, evidence=evidence, max_hops=4, critic_mode=mode
    )


def test_tiered_skips_llm_when_graph_conclusion_is_decisive() -> None:
    llm = CountingLLM()
    result = critique(_ctx("tiered", [_graph_hit()]), llm, allow_llm=True)
    assert llm.calls == 0
    assert result.action is CriticAction.SUFFICIENT
    assert "critic_tier=deterministic" in result.rationale


def test_tiered_escalates_on_text_only_evidence() -> None:
    llm = CountingLLM()
    text = Candidate(id="t", source=CandidateSource.FULLTEXT, content="Apex is big", score=1.0)
    result = critique(_ctx("tiered", [text]), llm, allow_llm=True)
    assert llm.calls == 1
    assert "critic_tier=llm" in result.rationale


def test_llm_mode_keeps_v1_behaviour_and_offline_never_calls() -> None:
    llm = CountingLLM()
    critique(_ctx("llm", [_graph_hit()]), llm, allow_llm=True)
    assert llm.calls == 1
    critique(_ctx("offline", [_graph_hit()]), llm, allow_llm=True)
    assert llm.calls == 1


def test_mode_validation_and_config_plumbing() -> None:
    assert normalize_mode(None) == "llm"
    assert normalize_mode(" Tiered ") == "tiered"
    with pytest.raises(ValueError):
        normalize_mode("sometimes")
    cfg = AppConfig()
    cfg.guardrails.critic_mode = "tiered"
    gc = GuardrailConfig.from_app_config(cfg)
    assert gc.critic_mode == "tiered"
    assert gc.with_overrides(max_hops=2).critic_mode == "tiered"
