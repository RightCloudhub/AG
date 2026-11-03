"""A failed retrieval channel stays visible without discarding healthy evidence."""

import pytest
from test_multi_runtime import setup_run

from agentic_graphrag.agent.executor import ToolCallSpec
from agentic_graphrag.agent.loop import invoke_config
from agentic_graphrag.agent.multi_graph import build_multi_graph
from agentic_graphrag.agent.requirements import Requirement
from agentic_graphrag.agent.research_cache import QueryDedup, scoped_key
from agentic_graphrag.agent.research_state import ResearchTask
from agentic_graphrag.agent.research_tools import EvidenceScopeError, retrieve
from agentic_graphrag.core.enums import RetrievalTool
from agentic_graphrag.core.evidence import Candidate, CandidateSource


def test_channel_failure_is_not_cached_and_healthy_tools_complete(monkeypatch):
    deps, config, state, _ = setup_run(monkeypatch)
    monkeypatch.setattr(
        deps.executor,
        "_choose_tools",
        lambda q, *_a, **_k: [
            ToolCallSpec(tool=RetrievalTool.VECTOR_SEARCH, args={"query": q}),
            ToolCallSpec(tool=RetrievalTool.FULLTEXT_SEARCH, args={"query": q}),
        ],
    )

    def fail(**_kwargs):
        raise RuntimeError("unavailable")

    deps.executor.tools.get(RetrievalTool.VECTOR_SEARCH).handler = fail
    result = build_multi_graph(deps, config).invoke(state, invoke_config("degraded"))
    assert result["register"].is_clear()
    assert result["chain"].claims[0].evidence_ids == ["c1"]
    assert all("vector_search" not in key for key in result["query_cache"])
    assert deps.executor.health.is_degraded()
    proposal = next(p for p in result["proposals"] if p.round)
    assert "degraded" in proposal.tool_calls[0].reason


def test_tenant_mismatch_is_fatal_even_if_other_tools_work(monkeypatch):
    deps, config, state, _ = setup_run(monkeypatch)
    foreign = Candidate(
        id="private",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="secret",
        metadata={"tenant_id": "other"},
    )
    deps.executor.tools.get(RetrievalTool.FULLTEXT_SEARCH).handler = lambda **_k: [foreign]
    with pytest.raises(EvidenceScopeError):
        build_multi_graph(deps, config).invoke(state, invoke_config("scope"))


def test_cached_evidence_is_checked_before_researcher_input(monkeypatch):
    deps, _, _, calls = setup_run(monkeypatch)
    task = ResearchTask(
        branch_id="branch",
        run_id="run",
        tenant_id="tenant",
        question="need",
        requirement=Requirement(req_id="a", text="need"),
        round=1,
        token_limit=1000,
        call_limit=1,
        tool_limit=1,
    )
    foreign = Candidate(
        id="private",
        source=CandidateSource.FULLTEXT_CHUNK,
        content="secret",
        metadata={"tenant_id": "other"},
    )
    key = scoped_key("tenant", "run", tool="fulltext_search", args={"query": "need"})
    cache = QueryDedup()
    cache.prime({key: [foreign]})
    with pytest.raises(EvidenceScopeError):
        retrieve(deps.executor, task, cache, query="need", limit=1)
    assert calls == []
