"""The recursion floor covers every run the guards allow, so it stays a true backstop (ORCH-08)."""

from __future__ import annotations

import hashlib
import os
import random

import pytest

from agentic_graphrag.agent import loop_runtime
from agentic_graphrag.agent.critic import CriticResult
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import (
    MAX_ANSWER_RETRIEVALS,
    MAX_GAP_SUB_QUESTIONS,
    GuardrailConfig,
    min_recursion_limit,
)
from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.agent.options import CritiqueContext
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.config import GuardrailsConfig, get_config
from agentic_graphrag.core.enums import CriticAction
from agentic_graphrag.core.evidence import Candidate, CandidateSource
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.generation.verdict import EVIDENCE_GAP_KEY
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

QUESTION = "Who leads the supplier of Apex Holdings?"
FUZZ_RUNS = int(os.environ.get("AGR_RECURSION_FUZZ_RUNS", "150"))
FUZZ_MAX_HOPS = 8
ADVANCE_RATE = 0.8
# 20 is ``hard_max_hops``, the largest hop budget a request can reach.
WORST_HOPS = [1, 2, 3, 4, 5, 20]


def _question(seed: object) -> str:
    """Text no other question in a run is a near-duplicate of."""
    digest = hashlib.sha256(str(seed).encode()).hexdigest()
    return f"{digest[:12]} {digest[12:24]} {digest[24:36]}"


def _executor(monkeypatch) -> Executor:
    executor = Executor(GraphRetriever(InMemoryGraphStore()))

    def run(sub_question: str, *_args, **_kwargs):
        hit = Candidate(
            id=f"hit:{sub_question}",
            source=CandidateSource.FULLTEXT_CHUNK,
            content=f"Evidence about {sub_question}",
            score=1.0,
        )
        return [hit], []

    monkeypatch.setattr(executor, "run", run)
    return executor


def _reports_one_gap(chain: ReasoningChain, *_args, report_evidence_gaps: bool, **_kwargs):
    """A composer that sends the run back to retrieval the one time it may."""
    updated = chain.model_copy(deep=True)
    if report_evidence_gaps:
        updated.metadata = {
            **(updated.metadata or {}),
            EVIDENCE_GAP_KEY: {
                "reason": "claim not entailed",
                "missing": [_question(f"gap {i}") for i in range(MAX_GAP_SUB_QUESTIONS)],
            },
        }
    return updated


def _guards(max_hops: int, max_sub_questions: int, *, parallel: bool) -> GuardrailConfig:
    # A recursion limit of 1 leaves only the computed floor in force.
    return GuardrailConfig.from_app_config(
        GuardrailsConfig(
            max_hops=max_hops,
            max_sub_questions=max_sub_questions,
            parallel_branches=parallel,
            recursion_limit=1,
        )
    )


def _run(executor: Executor, guards: GuardrailConfig) -> ReasoningChain:
    return run_agentic_query(QUESTION, executor, None, allow_llm=False, guard_cfg=guards)


def _random_verdict(rng: random.Random) -> CriticResult:
    # Mostly advance the plan: long runs are the ones that test the floor.
    action = (
        CriticAction.SUFFICIENT if rng.random() < ADVANCE_RATE else rng.choice(list(CriticAction))
    )
    new_question = _question(rng.random())
    return CriticResult(
        action=action,
        new_sub_question=new_question if action != CriticAction.SUFFICIENT else None,
        partial_answer=rng.choice([None, "Harbor Components"]),
        sub_answered=rng.random() < 0.5,
    )


def _random_plan(rng: random.Random, size: int) -> list[SubQuestion]:
    nodes: list[SubQuestion] = []
    for i in range(1, size + 1):
        text = _question(rng.random())
        depends_on: list[str] = []
        if i > 1 and rng.random() < 0.4:
            text = f"{text} {{from:sq{i - 1}}}"
            depends_on = [f"sq{i - 1}"]
        elif rng.random() < 0.15 and nodes:
            text = nodes[-1].text  # A duplicate the executor will skip.
        nodes.append(SubQuestion(id=f"sq{i}", text=text, depends_on=depends_on))
    return nodes


def test_randomized_runs_never_reach_the_recursion_backstop(monkeypatch) -> None:
    rng = random.Random(20261104)
    monkeypatch.setattr(loop_runtime, "generate_answer", _reports_one_gap)
    monkeypatch.setattr(loop_runtime, "critique", lambda *_args, **_kwargs: _random_verdict(rng))
    executor = _executor(monkeypatch)
    hits = []
    for run in range(FUZZ_RUNS):
        max_hops = rng.randint(1, FUZZ_MAX_HOPS)
        breadth = rng.randint(max(1, max_hops - 1), max_hops)
        plan = _random_plan(rng, breadth)
        monkeypatch.setattr(loop_runtime, "plan", lambda *_args, plan=plan, **_kwargs: list(plan))
        guards = _guards(max_hops, breadth, parallel=rng.random() < 0.5)
        chain = _run(executor, guards)
        if chain.metadata.get("recursion_limit_hit"):
            hits.append((run, max_hops, breadth, [n.text for n in plan]))
    assert hits == []


@pytest.mark.parametrize("max_hops", WORST_HOPS)
@pytest.mark.parametrize("parallel", [True, False])
def test_widest_plan_with_a_back_edge_fits_the_floor(monkeypatch, max_hops, parallel) -> None:
    plan = [SubQuestion(id=f"sq{i}", text=_question(i)) for i in range(1, max_hops + 1)]
    monkeypatch.setattr(loop_runtime, "plan", lambda *_args, **_kwargs: list(plan))
    monkeypatch.setattr(loop_runtime, "generate_answer", _reports_one_gap)

    def sufficient(ctx: CritiqueContext, *_args, **_kwargs) -> CriticResult:
        return CriticResult(action=CriticAction.SUFFICIENT, partial_answer="Harbor")

    monkeypatch.setattr(loop_runtime, "critique", sufficient)

    chain = _run(_executor(monkeypatch), _guards(max_hops, max_hops, parallel=parallel))

    assert not chain.metadata.get("recursion_limit_hit")
    assert max_hops <= len(chain.steps) <= max_hops + MAX_GAP_SUB_QUESTIONS


def test_floor_accounts_for_the_answer_back_edge() -> None:
    hops = 4
    # Planner + an executor/critic pair per node (plan + gap nodes) + one terminal
    # executor visit and one answer per pass; LangGraph needs one above the step count.
    node_visits = hops + MAX_GAP_SUB_QUESTIONS * MAX_ANSWER_RETRIEVALS
    passes = 1 + MAX_ANSWER_RETRIEVALS
    assert min_recursion_limit(hops) == 1 + 2 * node_visits + 2 * passes + 1


def test_shipped_config_is_not_below_its_floor() -> None:
    guardrails = get_config().guardrails
    assert guardrails.recursion_limit >= min_recursion_limit(guardrails.max_hops)
