"""Offline stubs exercise actual Send graphs, local checkpoints and merge gates."""

import json
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agentic_graphrag.agent.blackboard import Fact, Proposal, usage_totals
from agentic_graphrag.agent.executor import Executor, ToolCallSpec
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import invoke_config, run_query
from agentic_graphrag.agent.multi_graph import build_multi_graph
from agentic_graphrag.agent.multi_merge import merge
from agentic_graphrag.agent.multi_supervisor import stop_reason, supervise
from agentic_graphrag.agent.options import AgentDeps, QueryOptions
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.agent.research_state import VerificationResult
from agentic_graphrag.agent.state import initial_state
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.enums import RequirementStatus, RetrievalTool
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.llm.provider import MockLLMProvider
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


class ResearchLLM(MockLLMProvider):
    def __init__(self, requirements=None, *, reject=False):
        super().__init__()
        self.requirements = requirements or {"a": {"text": "Who leads Acme?"}}
        self.reject = reject
        self.research_calls = []
        self.verifier_inputs = []

    def complete(self, messages, **kwargs):
        if self.budget:
            self.budget.record_call(10, 5)
        user = messages[-1].content
        if "Research exactly" in user:
            data = json.loads(user.split("Input:\n", 1)[1])
            item = data["requirement"]
            self.research_calls.append(item["req_id"])
            ids = [p["id"] for p in data["passages"]]
            item.update(
                status="resolved",
                passages=ids,
                touched_passages=ids,
                notes="Ada leads Acme.",
                anchors=["Acme"],
            )
            return json.dumps({"requirement": item})
        if "Independently verify every" in user:
            data = json.loads(user.split("Input:\n", 1)[1])
            self.verifier_inputs.append(data)
            return json.dumps(
                {
                    "verdicts": [
                        dict(
                            proposal_id=p["proposal_id"],
                            entailed=not self.reject,
                            reason="evidence",
                        )
                        for p in data
                    ]
                }
            )
        if "requirement_register" in user and "seed" in user:
            return json.dumps({"requirement_register": self.requirements})
        return json.dumps(
            {
                "answer": "Ada leads Acme.",
                "status": "answered",
                "claims": [{"text": "Ada leads Acme.", "evidence_ids": ["c1"]}],
            }
        )


def setup_run(monkeypatch, *, llm=None, config=None):
    executor = Executor(GraphRetriever(InMemoryGraphStore()))
    calls = []
    hit = Candidate(
        id="c1",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="Ada leads Acme.",
        metadata={"tenant_id": "tenant"},
    )
    monkeypatch.setattr(
        executor,
        "_choose_tools",
        lambda query, *_args, **_kwargs: [
            ToolCallSpec(tool=RetrievalTool.FULLTEXT_SEARCH, args={"query": query})
        ],
    )

    def handler(**kwargs):
        calls.append(kwargs["tenant_id"])
        return [hit]

    executor.tools.get(RetrievalTool.FULLTEXT_SEARCH).handler = handler
    config = config or AgentConfig(mode="multi")
    guards = GuardrailConfig(max_hops=4, max_llm_calls=30, max_tokens=100_000)
    llm = llm or ResearchLLM()
    deps = AgentDeps(executor, llm, guards)
    state = initial_state(
        "Who leads Acme?",
        ReasoningChain(question="Who leads Acme?", query_id="run"),
        allow_llm=True,
        tenant_id="tenant",
    )
    return deps, config, state, calls


def test_serial_research_graph_is_grounded_metered_and_checkpointed(monkeypatch):
    deps, config, state, calls = setup_run(monkeypatch)
    backend = MemorySaver()
    graph = build_multi_graph(deps, config, checkpointer=backend)
    result = graph.invoke(state, invoke_config("test", recursion_limit=40))
    assert result["register"].items["a"].status == RequirementStatus.RESOLVED
    assert result["register_runtime"].stop == "clear"
    assert result["chain"].claims[0].evidence_ids == ["c1"]
    assert calls == ["tenant"]
    assert deps.llm.research_calls == ["a"]
    assert usage_totals(result["budget_ledger"])["llm_calls"] == 4
    assert set(deps.llm.verifier_inputs[0][0]) == {"proposal_id", "requirement", "passages"}
    restored = graph.get_state(invoke_config("test")).values
    assert isinstance(restored["proposals"][-1], Proposal)
    assert restored["register"] == result["register"]


def test_rejection_cap_retains_missing_information(monkeypatch):
    deps, config, state, _ = setup_run(monkeypatch, llm=ResearchLLM(reject=True))
    result = build_multi_graph(deps, config).invoke(
        state, invoke_config("rejected", recursion_limit=40)
    )
    assert result["register"].items["a"].status == RequirementStatus.RETIRED
    assert result["register_runtime"].stop == "verifier_rejection_cap"
    assert result["register_runtime"].rejections == {"a": 2}
    assert "Who leads Acme?" in result["chain"].missing_info
    assert result["chain"].status.value != "answered"


def test_dependencies_are_scheduled_after_approved_parent(monkeypatch):
    llm = ResearchLLM(
        {
            "a": {"text": "Who leads Acme?"},
            "b": {"text": "Who leads Acme now?", "depends_on": ["a"]},
        }
    )
    deps, config, state, _ = setup_run(monkeypatch, llm=llm)
    result = build_multi_graph(deps, config).invoke(
        state, invoke_config("deps", recursion_limit=40)
    )
    assert llm.research_calls == ["a", "b"]
    assert [p.round for p in result["proposals"] if p.round] == [1, 2]
    assert result["register"].is_clear()


def test_multi_flag_does_not_change_offline_path(monkeypatch):
    deps, config, _, _ = setup_run(monkeypatch)
    monkeypatch.setattr(
        "agentic_graphrag.agent.multi_graph.build_multi_graph",
        lambda *_a, **_k: pytest.fail("offline entered multi"),
    )
    answer = run_query(
        "Who leads Acme?",
        deps.executor,
        None,
        options=QueryOptions(
            allow_llm=False, force_agentic=True, agent_config=config, guard_cfg=deps.guard_cfg
        ),
    )
    assert "agent_mode" not in answer.metadata


def test_merge_creates_conflict_and_rejects_cross_tenant(monkeypatch):
    _, _, state, _ = setup_run(monkeypatch)
    state["hop"] = 1
    state["register"] = Register(
        question=state["question"],
        items={rid: Requirement(req_id=rid, text="Who leads Acme?") for rid in ("a", "b")},
    )
    for rid, value in (("a", "Ada"), ("b", "Ben")):
        state["evidence"][rid] = Candidate(
            id=rid, source=CandidateSource.FULLTEXT_CHUNK, content=f"{value} leads Acme."
        )
        state["proposals"].append(
            Proposal(
                entry_id=rid,
                branch_id=rid,
                run_id="run",
                tenant_id="tenant",
                round=1,
                requirement=Requirement(
                    req_id=rid,
                    text="Who leads Acme?",
                    status=RequirementStatus.RESOLVED,
                    passages={rid},
                    touched_passages={rid},
                ),
                facts=[Fact(entity="Acme", relation="leader", value=value, passages={rid})],
            )
        )
        state["verifications"].append(
            VerificationResult(proposal_id=rid, entailed=True, reason="yes")
        )
    result = merge(state, rejection_cap=2)
    assert len(result["conflicts"]) == 1
    conflict = next(iter(result["conflicts"]))
    assert result["register"].items[conflict].passages == {"a", "b"}
    assert not result["register"].is_clear()
    tasks = supervise(result, config=AgentConfig(mode="multi"), guards=GuardrailConfig(max_hops=4))
    assert [task.requirement.req_id for task in tasks["tasks"]] == [conflict]
    state["proposals"][0].tenant_id = "wrong"
    with pytest.raises(ValueError, match="cross-run or cross-tenant"):
        merge(state, rejection_cap=2)


def test_explicit_stop_reasons(monkeypatch):
    deps, _, state, _ = setup_run(monkeypatch)
    state["register"] = Register(question="q", items={"a": Requirement(req_id="a", text="need")})
    state["hop"] = 4
    assert stop_reason(state, guards=deps.guard_cfg) == "round_budget"
    state["hop"] = 0
    assert stop_reason(state, guards=replace(deps.guard_cfg, max_tokens=0)) == "token_budget"
    state["register"].items["b"] = Requirement(req_id="b", text="need", depends_on=["a"])
    state["register"].items["a"].status = RequirementStatus.RETIRED
    assert stop_reason(state, guards=deps.guard_cfg) == "dependency_blocked"


@pytest.mark.parametrize("max_parallel", [1, 3])
def test_failed_branch_resumes_alone_with_a_new_runtime(monkeypatch, max_parallel):
    class FailsOnce(ResearchLLM):
        def __init__(self):
            super().__init__(
                {"a": {"text": "Who leads Acme?"}, "b": {"text": "Who leads Acme now?"}}
            )
            self.failures = []

        def complete(self, messages, **kwargs):
            user = messages[-1].content
            if "Research exactly" in user:
                data = json.loads(user.split("Input:\n", 1)[1])
                if data["requirement"]["req_id"] == "b" and not self.failures:
                    self.failures.append("b")
                    raise RuntimeError("temporary model failure")
            return super().complete(messages, **kwargs)

    deps, config, state, calls = setup_run(
        monkeypatch, llm=FailsOnce(), config=AgentConfig(mode="multi", max_parallel=max_parallel)
    )
    backend = MemorySaver()
    invocation = invoke_config("resume", recursion_limit=40)
    graph = build_multi_graph(deps, config, checkpointer=backend)
    with pytest.raises(RuntimeError, match="temporary model failure"):
        graph.invoke(state, invocation)
    result = build_multi_graph(deps, config, checkpointer=backend).invoke(None, invocation)
    assert result["register"].is_clear()
    assert sorted(deps.llm.research_calls) == ["a", "b"]
    assert calls == ["tenant", "tenant"]
    assert len([p for p in result["proposals"] if p.round]) == 2
