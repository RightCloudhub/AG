"""GraphRecursionError must yield a partial/honest answer, not a bare error event."""

from __future__ import annotations

import json

from agentic_graphrag.agent import critic as critic_mod
from agentic_graphrag.agent.critic import CriticAction, CriticResult, CriticScope
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig, min_recursion_limit
from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.api.service_stream import stream_query_events
from agentic_graphrag.config import GuardrailsConfig, resolve_path
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph, triples_to_records
from agentic_graphrag.knowledge.schema_check import Triple
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def test_min_recursion_limit_covers_worst_case_path() -> None:
    # planner + (max_hops+1)*(exec+critic) + answer needs limit > visit count
    assert min_recursion_limit(4) >= 13
    assert min_recursion_limit(5) >= 15


def test_from_app_config_floors_low_recursion_limit() -> None:
    raw = GuardrailsConfig(
        max_hops=4,
        max_llm_calls=16,
        max_tokens_per_query=50000,
        query_timeout_seconds=45,
        recursion_limit=8,  # too low for hop budget
    )
    g = GuardrailConfig.from_app_config(raw)
    assert g.recursion_limit >= min_recursion_limit(4)


def test_stream_recursion_yields_answer_not_error(monkeypatch) -> None:
    """Even with a stubborn critic and tight limit, SSE ends with answer."""
    calls = {"n": 0}

    def always_next(ctx, llm=None):  # noqa: ANN001
        calls["n"] += 1
        return CriticResult(
            action=CriticAction.NEXT_HOP,
            scope=CriticScope.SUB_QUESTION,
            rationale="force continue",
            new_sub_question=(
                f"unique follow-up {calls['n']} with enough length to avoid near-dup"
            ),
            sub_answered=False,
            global_answered=False,
        )

    monkeypatch.setattr(critic_mod, "offline_critique", always_next)
    monkeypatch.setattr(critic_mod, "_llm_critique", always_next)

    svc = QueryService.create_offline()
    question = "Apex Holdings 的 CEO 所在公司，后来收购了哪家公司？那家被收购公司的总部在哪个城市"
    req = QueryRequest(question=question, force_agentic=True)

    from agentic_graphrag.api import service_stream as ss

    orig = ss._guard_and_budget

    def tight_guard(svc_in, req_in):  # noqa: ANN001
        g, b = orig(svc_in, req_in)
        # Direct GuardrailConfig bypasses floor — recovery path must still answer.
        g = GuardrailConfig(
            max_hops=10,
            max_llm_calls=g.max_llm_calls,
            max_tokens=g.max_tokens,
            query_timeout_seconds=g.query_timeout_seconds,
            recursion_limit=6,
        )
        return g, b

    monkeypatch.setattr(ss, "_guard_and_budget", tight_guard)

    events = list(stream_query_events(svc, req, tenant_id="t", user_id="u"))
    types = [e for e, _ in events]
    assert "answer" in types
    assert types[-1] == "answer"
    answer_payload = next(p for e, p in events if e == "answer")
    assert answer_payload.get("answer")
    meta = answer_payload.get("metadata") or {}
    assert meta.get("recursion_limit_hit") is True
    svc.close()


def test_run_agentic_recovers_on_recursion(monkeypatch) -> None:
    triples = [
        Triple.model_validate(json.loads(line))
        for line in resolve_path("data/processed/seed_triples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    store = InMemoryGraphStore()
    load_triples_into_graph(store, triples, clear_first=True)
    entities, _ = triples_to_records(triples)
    known = sorted({e.name for e in entities}, key=lambda s: (-len(s), s.lower()))
    executor = Executor(
        graph=GraphRetriever(store, max_neighbors_per_layer=50, max_paths=20),
        vector=None,
        fulltext=None,
        llm=None,
        known_entities=known,
    )

    calls = {"n": 0}

    def always_next(ctx):  # noqa: ANN001
        calls["n"] += 1
        return CriticResult(
            action=CriticAction.NEXT_HOP,
            new_sub_question=f"unique next {calls['n']} padding length enough here yes",
            rationale="x",
        )

    monkeypatch.setattr(critic_mod, "offline_critique", always_next)

    chain = run_agentic_query(
        "Who is the CEO of Apex Holdings and what did it acquire?",
        executor,
        None,
        guard_cfg=GuardrailConfig(
            max_hops=8,
            max_llm_calls=50,
            max_tokens=100_000,
            recursion_limit=6,
        ),
        allow_llm=False,
        recursion_limit=6,
    )
    assert chain.answer
    assert chain.metadata.get("recursion_limit_hit") is True
