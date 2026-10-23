"""Deterministic multi-hop, audit, budgets and SSE register regressions."""

import json

import pytest

from agentic_graphrag.agent.executor import Executor, ToolCallSpec
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.agent.loop_runtime import AgentRuntime
from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.agent.pipeline import iter_query_progress
from agentic_graphrag.agent.state import initial_state
from agentic_graphrag.config_register import RequirementRegisterConfig
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.llm.provider import MockLLMProvider, Tier
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

QUESTION = "Who owns Acme and where is the owner's headquarters?"
CONTENT = "Acme is owned by Bridge. Bridge is based in Santa Monica."


class ScriptLLM(MockLLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.script = list(responses)
        self.options = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        self.options.append(kwargs)
        response = self.script.pop(0)
        return response if isinstance(response, str) else json.dumps(response)


class Retriever:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query, top_k=None, *, tenant_id=None):
        self.calls.append((query, top_k, tenant_id))
        return self.hits


def setup_runtime(monkeypatch, responses=(), **config_overrides):
    seed = Retriever([Candidate(id="seed", source="vector", content="Acme is a company.")])
    fulltext = Retriever([Candidate(id="c18", source="fulltext", content=CONTENT)])
    llm = ScriptLLM(responses)
    executor = Executor(GraphRetriever(InMemoryGraphStore()), vector=seed, fulltext=fulltext)
    monkeypatch.setattr(
        executor,
        "_choose_tools",
        lambda q, hints, **kw: [
            ToolCallSpec(tool="fulltext_search", args={"query": q}, reason="keyword recall")
        ],
    )
    config = RequirementRegisterConfig(enabled=True, **config_overrides)
    guards = GuardrailConfig(max_hops=3, requirement_register=config)
    return executor, llm, guards, seed, fulltext


def initialization():
    return {
        "evidence_summary": "Acme is a company",
        "requirement_register": {
            "owner": {"text": "Who owns Acme?", "status": "active"},
            "city": {"text": "Where is the owner's headquarters?", "depends_on": ["owner"]},
        },
    }


def resolved_update():
    return {
        "requirement_register": {
            "owner": {
                "text": "Who owns Acme?",
                "status": "resolved",
                "evidence_notes": "Bridge owns Acme",
                "passages": ["c18"],
                "touched_passages": ["c18"],
                "anchors": ["Bridge"],
            },
            "city": {
                "text": "Where is the owner's headquarters?",
                "status": "resolved",
                "evidence_notes": "Bridge is based in Santa Monica",
                "passages": ["c18"],
                "touched_passages": ["c18"],
                "depends_on": ["owner"],
            },
        }
    }


def answer():
    return {
        "answer": CONTENT,
        "status": "answered",
        "claims": [{"text": CONTENT, "evidence_ids": ["c18"]}],
    }


def test_one_chunk_closes_two_hops_and_is_fully_auditable(monkeypatch):
    responses = [
        initialization(),
        resolved_update(),
        {"entailed": True, "reason": "explicit owner"},
        {"entailed": True, "reason": "explicit headquarters"},
        answer(),
    ]
    executor, llm, guards, seed, fulltext = setup_runtime(monkeypatch, responses)
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards, tenant_id="tenant-a")
    assert chain.status == "answered"
    assert chain.metadata["register_rounds"] == 1
    assert chain.metadata["register_stop"] == "clear"
    assert set(chain.metadata["register_retrieved_ids"]) == {"seed", "c18"}
    event = chain.metadata["register_events"][1]
    assert {c["req_id"] for c in event["changes"] if c["after"] == "resolved"} == {"owner", "city"}
    assert seed.calls[0][1:] == (5, "tenant-a")
    assert fulltext.calls[0][2] == "tenant-a" and QUESTION in fulltext.calls[0][0]
    assert all(call["temperature"] == 0 for call in llm.options[:4])
    assert llm.options[2]["tier"] == Tier.LIGHT
    assert "Acme is a company" not in llm.calls[2][-1].content


@pytest.mark.parametrize("verdict", [{"entailed": False, "reason": "mere mention"}, "invalid JSON"])
def test_verifier_rejects_false_resolution_and_errors_fail_closed(monkeypatch, verdict):
    responses = [
        initialization(),
        resolved_update(),
        verdict,
        {"entailed": True, "reason": "city explicit"},
        answer(),
    ]
    executor, llm, guards, _, fulltext = setup_runtime(monkeypatch, responses, max_rounds=1)
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards)
    assert chain.status == "partial"
    assert "Who owns Acme?" in chain.missing_info
    assert chain.metadata["register_stop"] == "round_budget"
    assert chain.metadata["requirement_register"]["items"]["owner"]["status"] == "active"
    assert len(fulltext.calls) == 1


def test_duplicate_queries_and_seen_chunks_are_not_reissued(monkeypatch):
    executor, llm, guards, _, fulltext = setup_runtime(
        monkeypatch, [initialization(), {"requirement_register": {}}, answer()]
    )
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards)
    assert len(fulltext.calls) == 1
    assert len(llm.calls) == 3
    assert chain.metadata["register_rounds"] == 3
    assert chain.metadata["register_stop"] == "round_budget"
    assert chain.status == "partial"


def test_invalid_update_cannot_drop_requirements(monkeypatch):
    executor, llm, guards, _, _ = setup_runtime(
        monkeypatch, [initialization(), "invalid JSON", answer()], max_rounds=1
    )
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards)
    assert set(chain.metadata["requirement_register"]["items"]) == {"owner", "city"}
    assert (
        chain.metadata["register_events"][1]["verifier"][0]["error"] == "JSONDecodeError"
        or chain.metadata["register_events"][1]["verifier"][0]["error"] == "ValueError"
    )


def test_no_llm_keeps_original_path_even_when_register_config_is_enabled(monkeypatch):
    executor, llm, guards, _, fulltext = setup_runtime(monkeypatch)
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards, allow_llm=False)
    baseline = run_agentic_query(
        QUESTION, executor, llm, guard_cfg=GuardrailConfig(max_hops=3), allow_llm=False
    )
    assert not llm.calls
    assert len(fulltext.calls) == 2
    assert chain.answer == baseline.answer and chain.status == baseline.status
    assert "requirement_register" not in chain.metadata


def test_context_ceiling_stops_before_search_or_llm(monkeypatch):
    executor, llm, guards, _, fulltext = setup_runtime(monkeypatch, context_limit_tokens=1)
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards)
    assert not llm.calls and not fulltext.calls
    assert chain.metadata["register_stop"] == "context_limit"
    assert chain.missing_info == [QUESTION]


def test_stream_and_nonstream_share_register_progress(monkeypatch):
    script = [
        initialization(),
        resolved_update(),
        {"entailed": True, "reason": "owner"},
        {"entailed": True, "reason": "city"},
        answer(),
    ]
    executor, llm, guards, _, _ = setup_runtime(monkeypatch, script)
    events = list(
        iter_query_progress(
            QUESTION, executor, llm, options=QueryOptions(guard_cfg=guards, force_agentic=True)
        )
    )
    assert any(t == "thinking" and p.get("stage") == "progress" for t, p in events)
    assert events[-1][1].metadata["register_stop"] == "clear"


def test_checkpoint_state_keeps_register_cache_and_can_resume(monkeypatch):
    executor, llm, guards, _, fulltext = setup_runtime(
        monkeypatch, [initialization(), {"requirement_register": {}}]
    )
    runtime = AgentRuntime(executor, llm, guards)
    state = initial_state(QUESTION, ReasoningChain(question=QUESTION).model_dump(), allow_llm=True)
    state = runtime.node_critic(runtime.node_executor(runtime.node_planner(state)))
    resumed = AgentRuntime(executor, llm, guards)
    state = resumed.node_executor(state)
    assert state["hop"] == 2 and len(fulltext.calls) == 1
    assert state["register_seen"] == ["c18", "seed"]


def test_budget_during_verification_keeps_previous_open_state(monkeypatch):
    executor, llm, guards, _, _ = setup_runtime(monkeypatch, [initialization(), resolved_update()])
    budget = BudgetTracker(max_llm_calls=2)
    complete = llm.complete

    def metered(messages, **kwargs):
        budget.record_call(10, 5)
        return complete(messages, **kwargs)

    monkeypatch.setattr(llm, "complete", metered)
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards, budget=budget)
    assert chain.metadata["register_stop"] == "llm_budget"
    assert len(chain.missing_info) == 2
    assert len(llm.calls) == 2


@pytest.mark.parametrize(
    "option", ["seed_enabled", "updates_enabled", "verifier_enabled", "delta_enabled"]
)
def test_ablation_switches_keep_state_and_citation_gates(monkeypatch, option):
    script = [initialization()]
    if option != "updates_enabled":
        script.append(resolved_update())
        if option != "verifier_enabled":
            script.extend(
                [{"entailed": True, "reason": "owner"}, {"entailed": True, "reason": "city"}]
            )
    script.append(answer())
    executor, llm, guards, seed, _ = setup_runtime(
        monkeypatch, script, max_rounds=1, **{option: False}
    )
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards)
    assert chain.status == ("partial" if option == "updates_enabled" else "answered")
    if option == "seed_enabled":
        assert seed.calls == []
    assert chain.claims[0].evidence_ids == ["c18"]


def test_strict_abstention_is_configurable(monkeypatch):
    executor, llm, guards, _, _ = setup_runtime(
        monkeypatch,
        [initialization(), {"requirement_register": {}}, answer()],
        max_rounds=1,
        answer_policy="abstain",
    )
    chain = run_agentic_query(QUESTION, executor, llm, guard_cfg=guards)
    assert chain.status == "no_answer" and not chain.claims
    assert len(chain.missing_info) == 2
    assert len(llm.calls) == 2
