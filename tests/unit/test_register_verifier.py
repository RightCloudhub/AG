"""Verifier configuration, failure isolation and budget accounting."""

import json
import logging
from threading import Barrier

import pytest

from agentic_graphrag.agent.register_invariants import apply_invariants
from agentic_graphrag.agent.register_progress import verify_resolutions
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.config import AppConfig, Settings, build_llm_provider
from agentic_graphrag.config_register import RegisterVerifierConfig, RequirementRegisterConfig
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.llm.verifier import build_verifier
from agentic_graphrag.prompts import prompt_versions, reset_prompt_versions


def registers():
    old = Register(question="q", items={rid: Requirement(text=rid) for rid in ("a", "b")})
    new = Register(
        question="q",
        items={
            rid: Requirement(req_id=rid, text=rid, status="resolved", passages={"c1"})
            for rid in old.items
        },
    )
    evidence = {"c1": Candidate(id="c1", source="vector", content="support")}
    return old, new, evidence


@pytest.mark.parametrize(
    "enabled,light,judge,warning",
    [
        (True, "main", None, True),
        (True, "other", None, False),
        (True, "main", "other", False),
        (False, "main", None, False),
    ],
)
def test_provider_startup_warns_only_for_enabled_identical_models(
    caplog, enabled, light, judge, warning
):
    cfg = AppConfig()
    cfg.llm.strong_model, cfg.llm.light_model = "main", light
    cfg.guardrails.requirement_register = RequirementRegisterConfig(
        enabled=enabled, verifier=RegisterVerifierConfig(model=judge)
    )
    with caplog.at_level(logging.WARNING):
        provider = build_llm_provider(settings=Settings(_env_file=None), cfg=cfg)
    assert ("verifier uses the same model" in caplog.text) is warning
    if enabled:
        verifier = build_verifier(provider, cfg.guardrails.requirement_register.verifier)
        assert verifier is not provider
        assert verifier.light_model == (judge or light)


def test_batch_budget_refuses_calls_before_starting_and_does_not_change_counts():
    old, new, evidence = registers()
    budget = BudgetTracker(max_llm_calls=1)
    llm = MockLLMProvider(budget=budget)
    counts = {"a": 1}
    before = new.snapshot()
    with pytest.raises(BudgetExceeded):
        verify_resolutions(
            new,
            old,
            llm,
            evidence=evidence,
            context_limit=128_000,
            budget=budget,
            rejections=counts,
        )
    assert llm.calls == [] and budget.llm_calls == 0
    assert counts == {"a": 1} and new.snapshot() == before


def test_budget_error_in_one_judge_leaves_all_proposals_and_counts_untouched():
    old, new, evidence = registers()
    barrier = Barrier(2)
    budget = BudgetTracker()

    class BudgetLLM(MockLLMProvider):
        def complete(self, messages, **kwargs):
            barrier.wait(timeout=2)
            if '"requirement": "b"' in messages[-1].content:
                raise BudgetExceeded("test budget", budget)
            return json.dumps({"entailed": False, "reason": "unsupported"})

    counts = {"a": 1}
    before = new.snapshot()
    with pytest.raises(BudgetExceeded):
        verify_resolutions(
            new, old, BudgetLLM(), evidence=evidence, context_limit=128_000, rejections=counts
        )
    assert counts == {"a": 1} and new.snapshot() == before


@pytest.mark.parametrize("failure", ["missing_evidence", "context_limit", "invalid_json"])
def test_fail_closed_verdicts_count_toward_retirement_and_cannot_be_reopened(failure):
    old, new, evidence = registers()
    llm = MockLLMProvider(responses={"Independently verify": "invalid JSON"})
    counts = {"a": 1}
    reports = verify_resolutions(
        new,
        old,
        llm,
        evidence={} if failure == "missing_evidence" else evidence,
        context_limit=1 if failure == "context_limit" else 128_000,
        rejections=counts,
    )
    assert new.items["a"].status == "retired" and counts == {"a": 2, "b": 1}
    assert all(report["accepted"] is False for report in reports)
    if failure != "invalid_json":
        assert llm.calls == []
    proposal = new.model_copy(deep=True)
    proposal.items["a"] = Requirement(text="a", status="resolved", passages={"c1"})
    result = apply_invariants(proposal, new, evidence=evidence)
    assert result.items["a"].status == "retired"
    assert result.items["a"].retire_reason == "verifier rejected 2 times"


def test_concurrent_judges_preserve_prompt_version_audit():
    old, new, evidence = registers()
    reset_prompt_versions()
    llm = MockLLMProvider(responses={"Independently verify": '{"entailed": true, "reason": "yes"}'})
    verify_resolutions(new, old, llm, evidence=evidence, context_limit=128_000)
    assert "register_verify" in prompt_versions()
    assert "register_system" in prompt_versions()
