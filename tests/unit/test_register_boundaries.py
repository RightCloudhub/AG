"""Regression tests for evidence, independent verification and convergence boundaries."""

import json
import logging
from dataclasses import replace
from threading import Barrier

import pytest
from test_register_runtime import (
    CONTENT,
    QUESTION,
    initialization,
    resolved_update,
    setup_runtime,
)

from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.agent.loop_runtime import AgentRuntime
from agentic_graphrag.agent.register_answer import annotate_register
from agentic_graphrag.agent.register_progress import verify_resolutions
from agentic_graphrag.agent.register_state import RegisterRuntimeState
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.agent.state import initial_state
from agentic_graphrag.agent.state_serde import AgentStateSerializer
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import PromptName, RequirementStatus
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider, MockLLMProvider

SERDE = AgentStateSerializer()


def start(runtime):
    return runtime.node_planner(
        initial_state(QUESTION, ReasoningChain(question=QUESTION), allow_llm=True)
    )


def resume(state):
    """Round-trip every channel through the checkpoint codec, as a resumed run does."""
    return {key: SERDE.loads_typed(SERDE.dumps_typed(value)) for key, value in state.items()}


def with_fresh(state, hit=None):
    """Simulate a retrieval round that surfaced ``hit``, or nothing new."""
    evidence = dict(state["evidence"])
    if hit is not None:
        evidence[hit.id] = hit
    fresh = [hit.id] if hit is not None else []
    runtime = replace(state["register_runtime"], fresh=fresh)
    return {**state, "evidence": evidence, "register_runtime": runtime}


def test_verifier_uses_configured_model_and_isolated_prompt(monkeypatch):
    executor, _, guards, _, _ = setup_runtime(
        monkeypatch, verifier={"model": "independent-judge", "temperature": 0.3}
    )
    calls = []

    def post(self, path, payload, **kwargs):
        calls.append(payload)
        prompt = payload["messages"][-1]["content"]
        if "Independently verify" in prompt:
            response = {"entailed": True, "reason": "supported"}
        else:
            response = initialization() if len(calls) == 1 else resolved_update()
        return {"choices": [{"message": {"content": json.dumps(response)}}], "usage": {}}

    monkeypatch.setattr(LLMProvider, "_post", post)
    budget = BudgetTracker()
    llm = LLMProvider(api_key="test", strong_model="proposer", budget=budget)
    runtime = AgentRuntime(executor, llm, guards, budget=budget)
    state = runtime.node_critic(runtime.node_executor(start(runtime)))
    assert state["done"]
    assert [c["model"] for c in calls] == ["proposer", "proposer"] + ["independent-judge"] * 2
    assert all(c["temperature"] == 0.3 for c in calls[2:])
    assert llm.strong_model == "proposer" and llm.light_model == "gpt-4.1-mini"
    assert budget.llm_calls == 4
    for call in calls[2:]:
        prompt = json.dumps(call["messages"])
        assert CONTENT in prompt
        assert QUESTION not in prompt
        assert "Acme is a company" not in prompt
        assert "Bridge owns Acme" not in prompt
        assert '"role": "assistant"' not in prompt


def test_identical_verifier_model_warns_on_runtime_startup(monkeypatch, caplog):
    executor, llm, guards, _, _ = setup_runtime(monkeypatch)
    llm.strong_model = llm.light_model = "same-model"
    with caplog.at_level(logging.WARNING):
        runtime = AgentRuntime(executor, llm, guards)
        assert runtime.register_runtime.enabled({"allow_llm": True})
    assert "verifier" in caplog.text.lower() and "same-model" in caplog.text


@pytest.mark.parametrize("limit", [None, 1, 3])
def test_rejection_loop_retires_at_cap_and_survives_resume(monkeypatch, limit):
    cap = limit or 2
    init = {"requirement_register": {"owner": initialization()["requirement_register"]["owner"]}}
    script = [init]
    for hop in range(1, cap + 1):
        proposal = resolved_update()["requirement_register"]["owner"]
        proposal.update(passages=[f"c{hop}"], touched_passages=[f"c{hop}"])
        script.extend(
            [
                {"requirement_register": {"owner": proposal}},
                {"entailed": False, "reason": "not enough support"},
            ]
        )
    options = {"verifier": {"max_rejections": limit}} if limit else {}
    executor, llm, guards, _, _ = setup_runtime(monkeypatch, script, **options)
    guards = replace(guards, max_hops=10)
    runtime = AgentRuntime(executor, llm, guards)
    state = start(runtime)
    for hop in range(1, cap + 1):
        state = runtime.node_executor(state)
        hit = Candidate(id=f"c{hop}", source="vector", content=CONTENT)
        state = runtime.node_critic(with_fresh(state, hit))
        if hop < cap:
            assert not state["done"]
            state = resume(state)
            runtime = AgentRuntime(executor, llm, guards)
    requirement = state["register"].items["owner"]
    assert requirement.status == RequirementStatus.RETIRED
    assert requirement.retire_reason == f"verifier rejected {cap} times"
    register_runtime = state["register_runtime"]
    assert register_runtime.rejections == {"owner": cap}
    assert state["done"] and register_runtime.stop == "clear"
    report = register_runtime.events[-1]["verifier"][0]
    assert report["req_id"] == "owner" and report["retire_reason"] == requirement.retire_reason
    assert report["rejections"] == cap and report["accepted"] is False
    # Reports written before each resume keep their counts through the checkpoint codec.
    progress = [e for e in register_runtime.events if e["type"] == "progress"]
    assert [e["verifier"][0]["rejections"] for e in progress] == list(range(1, cap + 1))


@pytest.mark.parametrize("delta_enabled", [True, False])
def test_convergence_nudge_survives_skipped_hop_and_is_sent_once(monkeypatch, delta_enabled):
    executor, llm, guards, _, _ = setup_runtime(
        monkeypatch,
        [initialization(), {"requirement_register": {}}],
        convergence_ratio=0.5,
        delta_enabled=delta_enabled,
    )
    runtime = AgentRuntime(executor, llm, guards)
    state = runtime.node_executor(start(runtime))
    state = {**state, "hop": 3}  # Recovery skipped the threshold hop (ceil(0.5 * 3) == 2).
    state = runtime.node_critic(state)
    nudge = load_prompt(PromptName.REGISTER_BUDGET)
    assert sum(m.content == nudge for m in llm.calls[1]) == 1
    assert state["register_runtime"].budget_nudged is True
    runtime = AgentRuntime(executor, llm, guards)
    state = runtime.node_critic(resume(with_fresh(state)))
    assert sum(m["content"] == nudge for m in state["register_runtime"].context) <= 1
    assert state["register_runtime"].budget_nudged is True


def test_update_failure_logs_requirement_context(monkeypatch, caplog):
    executor, llm, guards, _, _ = setup_runtime(monkeypatch, [initialization(), "invalid JSON"])
    runtime = AgentRuntime(executor, llm, guards)
    state = runtime.node_executor(start(runtime))
    with caplog.at_level(logging.WARNING):
        result = runtime.node_critic(state)
    assert result["register"] == state["register"]
    failures = [r for r in caplog.records if getattr(r, "step", None) == "update"]
    assert len(failures) == 1 and failures[0].levelno == logging.WARNING
    assert failures[0].component == "requirement_register"
    assert "owner" in failures[0].req_ids and failures[0].hop == state["hop"]


def test_verifications_are_concurrent_and_reports_keep_requirement_order():
    barrier = Barrier(2)

    class ConcurrentLLM(MockLLMProvider):
        def complete(self, messages, **kwargs):
            barrier.wait(timeout=2)
            return json.dumps({"entailed": True, "reason": "supported"})

    old = Register(question="q", items={rid: Requirement(text=rid) for rid in ("a", "b")})
    new = old.model_copy(deep=True)
    for requirement in new.items.values():
        requirement.status = RequirementStatus.RESOLVED
        requirement.passages = {"c1"}
    reports = verify_resolutions(
        new,
        old,
        ConcurrentLLM(),
        evidence={"c1": Candidate(id="c1", source="vector", content=CONTENT)},
        context_limit=128_000,
    )
    assert [r["req_id"] for r in reports] == ["a", "b"]
    assert all(r["entailed"] for r in reports)


@pytest.mark.parametrize("policy,status", [("partial", "partial"), ("abstain", "no_answer")])
def test_verifier_retirement_stays_visible_as_missing_information(policy, status):
    requirement = Requirement(
        req_id="owner",
        text="Who owns Acme?",
        status="retired",
        passages={"c1"},
        retire_reason="verifier rejected 2 times",
    )
    register = Register(question=QUESTION, items={"owner": requirement})
    state = {
        "register": register,
        "register_runtime": RegisterRuntimeState(
            rejections={"owner": 2}, stop="clear", answer_policy=policy
        ),
    }
    chain = annotate_register(ReasoningChain(question=QUESTION, status="answered"), state)
    assert chain.status == status
    assert chain.missing_info == [requirement.text]
    assert chain.metadata["register_rejections"] == {"owner": 2}


@pytest.mark.parametrize("policy", ["partial", "abstain"])
def test_evidence_retirement_after_rejection_preserves_supported_answer(monkeypatch, policy):
    fresh = Candidate(
        id="c19",
        source="fulltext",
        content="Acme has no owner. Its headquarters is in Santa Monica.",
    )
    reason = "c19 contradicts the premise that Acme has an owner"
    script = [
        {"requirement_register": {"owner": initialization()["requirement_register"]["owner"]}},
        {"requirement_register": {"owner": resolved_update()["requirement_register"]["owner"]}},
        {"entailed": False, "reason": "ownership is not established"},
        {
            "requirement_register": {
                "owner": {
                    "text": "Who owns Acme?",
                    "status": "retired",
                    "retire_reason": reason,
                    "passages": [fresh.id],
                    "touched_passages": [fresh.id],
                },
                "city": {
                    "text": "Where is Acme's own headquarters?",
                    "status": "resolved",
                    "passages": [fresh.id],
                    "touched_passages": [fresh.id],
                },
            }
        },
        {"entailed": True, "reason": "headquarters is explicit"},
        {
            "answer": fresh.content,
            "status": "answered",
            "claims": [{"text": fresh.content, "evidence_ids": [fresh.id]}],
        },
    ]
    executor, llm, guards, _, _ = setup_runtime(monkeypatch, script, answer_policy=policy)
    runtime = AgentRuntime(executor, llm, guards)
    state = runtime.node_critic(runtime.node_executor(start(runtime)))
    assert state["register_runtime"].rejections == {"owner": 1} and not state["done"]
    runtime = AgentRuntime(executor, llm, guards)
    state = runtime.node_executor(resume(state))
    state = runtime.node_critic(with_fresh(state, fresh))
    owner = state["register"].items["owner"]
    assert owner.retire_reason == reason and owner.status == RequirementStatus.RETIRED
    assert state["register"].items["city"].status == RequirementStatus.RESOLVED
    assert state["done"] and state["register_runtime"].stop == "clear"
    chain = runtime.node_answer(state)["chain"]
    assert chain.status == "answered" and chain.missing_info == []
    assert chain.answer == fresh.content and chain.claims[0].evidence_ids == [fresh.id]
    assert chain.metadata["register_rejections"] == {"owner": 1}
    assert not llm.script


def test_no_llm_does_not_configure_an_unsupported_custom_verifier(monkeypatch):
    executor, llm, guards, _, _ = setup_runtime(
        monkeypatch, verifier={"model": "independent-judge"}
    )

    class CustomClient:
        complete = llm.complete
        embed = llm.embed
        embed_many = llm.embed_many

    chain = run_agentic_query(QUESTION, executor, CustomClient(), guard_cfg=guards, allow_llm=False)
    assert not llm.calls
    assert "requirement_register" not in chain.metadata
