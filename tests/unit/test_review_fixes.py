"""Regression tests for quality-refactor review fixes."""

from __future__ import annotations

from agentic_graphrag.agent.critic import (
    CriticAction,
    CriticResult,
    CriticScope,
    CritiqueContext,
    critique,
)
from agentic_graphrag.agent.critic_offline import extract_entity_conclusion
from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.agent.loop_handlers import CriticApplyCtx, apply_critic_result
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.eval.cases import CaseCategory, EvalCase
from agentic_graphrag.eval.gold_templates.context import EmitContext
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource


def test_critique_accepts_context_and_legacy_positional() -> None:
    empty_ctx = CritiqueContext(
        question="Who is CEO?",
        sub_question="Who is CEO of Apex?",
        evidence=[],
        hop=1,
        max_hops=3,
    )
    r1 = critique(empty_ctx, None, allow_llm=False)
    assert r1.action == CriticAction.NEXT_HOP

    r2 = critique("Who is CEO?", "Who is CEO of Apex?", [], [], None, hop=1, max_hops=3)
    assert r2.action == CriticAction.NEXT_HOP

    r3 = critique(
        "Who is CEO?",
        llm=None,
        sub_question="x",
        evidence=[],
        explored_paths=[],
        hop=9,
        max_hops=3,
    )
    assert r3.action == CriticAction.GIVE_UP


def test_employed_by_parse_path_not_person_shortcut() -> None:
    """Content-parse path must not treat EMPLOYED_BY like CEO_OF (pre-refactor)."""
    c = Candidate(
        id="e1",
        source=CandidateSource.GRAPH_PATH,
        content="Alice -[EMPLOYED_BY]-> Acme Corp",
        score=1.0,
    )
    out = extract_entity_conclusion("Where does Alice work?", [c])
    assert out is not None
    assert "Acme" in out or out == "Acme Corp"


def test_emit_context_try_add_only_counts_kept() -> None:
    kept: list[EvalCase] = []

    def add(case: EvalCase) -> bool:
        if case.question == "dup":
            return False
        kept.append(case)
        return True

    ctx = EmitContext([], {}, {}, add, max_n=10)
    ok = EvalCase(
        id="a",
        question="unique",
        gold_answer="x",
        hops=1,
        category=CaseCategory.OPEN,
    )
    bad = EvalCase(
        id="b",
        question="dup",
        gold_answer="x",
        hops=1,
        category=CaseCategory.OPEN,
    )
    assert ctx.try_add(ok) is True
    assert ctx.count == 1
    assert ctx.try_add(bad) is False
    assert ctx.count == 1


def test_apply_critic_advances_when_remaining() -> None:
    memory = MemoryState()
    guards = Guardrails(GuardrailConfig(max_hops=5))
    chain = ReasoningChain(question="q")
    state = {
        "question": "q",
        "chain": chain.model_dump(),
        "sub_questions": [
            {"id": "sq1", "text": "a", "depends_on": []},
            {"id": "sq2", "text": "b", "depends_on": []},
        ],
        "current_index": 0,
        "hop": 1,
        "evidence": [],
        "done": False,
        "allow_llm": False,
    }
    result = CriticResult(
        action=CriticAction.SUFFICIENT,
        scope=CriticScope.SUB_QUESTION,
        sub_answered=True,
        global_answered=False,
        rationale="ok",
    )
    out = apply_critic_result(
        CriticApplyCtx(
            state=state,
            result=result,
            sq=None,
            sq_text="a",
            sq_id="sq1",
            idx=0,
            remaining=1,
            memory=memory,
            guards=guards,
            guard_cfg=GuardrailConfig(max_hops=5),
        )
    )
    assert out["current_index"] == 1
    assert out.get("done") is False


def test_apply_critic_give_up_stops_despite_remaining() -> None:
    memory = MemoryState()
    guards = Guardrails(GuardrailConfig(max_hops=5))
    chain = ReasoningChain(question="q")
    state = {
        "question": "q",
        "chain": chain.model_dump(),
        "sub_questions": [
            {"id": "sq1", "text": "a", "depends_on": []},
            {"id": "sq2", "text": "b", "depends_on": []},
        ],
        "current_index": 0,
        "hop": 1,
        "evidence": [],
        "done": False,
        "allow_llm": False,
    }
    result = CriticResult(
        action=CriticAction.GIVE_UP,
        scope=CriticScope.GLOBAL,
        rationale="cannot answer",
    )
    out = apply_critic_result(
        CriticApplyCtx(
            state=state,
            result=result,
            sq=None,
            sq_text="a",
            sq_id="sq1",
            idx=0,
            remaining=1,
            memory=memory,
            guards=guards,
            guard_cfg=GuardrailConfig(max_hops=5),
        )
    )
    assert out.get("done") is True
    assert memory.is_excluded("a")


def test_extract_conclusion_prefers_ceo_over_parent_edge() -> None:
    parent = Candidate(
        id="p1",
        source=CandidateSource.GRAPH_PATH,
        content="Apex Holdings -[PARENT_OF]-> BrightLink Logistics",
        score=1.0,
        structured={
            "head": "Apex Holdings",
            "relation": "PARENT_OF",
            "tail": "BrightLink Logistics",
            "query_entity": "BrightLink Logistics",
        },
    )
    ceo = Candidate(
        id="c1",
        source=CandidateSource.GRAPH_PATH,
        content="Elena Varga -[CEO_OF]-> Apex Holdings",
        score=1.0,
        structured={
            "head": "Elena Varga",
            "relation": "CEO_OF",
            "tail": "Apex Holdings",
            "query_entity": "Apex Holdings",
        },
    )
    out = extract_entity_conclusion(
        "Who is the CEO of Apex Holdings?", [parent, ceo]
    )
    assert out == "Elena Varga"


def test_extract_conclusion_work_prefers_company_not_person() -> None:
    ceo = Candidate(
        id="c1",
        source=CandidateSource.GRAPH_PATH,
        content="Elena Varga -[CEO_OF]-> Apex Holdings",
        score=1.0,
        structured={
            "head": "Elena Varga",
            "relation": "CEO_OF",
            "tail": "Apex Holdings",
            "query_entity": "Apex Holdings",
        },
    )
    work = Candidate(
        id="w1",
        source=CandidateSource.GRAPH_PATH,
        content="Elena Varga -[WORKED_AT]-> Meridian Capital",
        score=1.0,
        structured={
            "head": "Elena Varga",
            "relation": "WORKED_AT",
            "tail": "Meridian Capital",
            "query_entity": "Elena Varga",
        },
    )
    out = extract_entity_conclusion(
        "Which companies did Elena Varga previously work at?", [ceo, work]
    )
    assert out == "Meridian Capital"


def test_memory_short_prefix_not_duplicate() -> None:
    m = MemoryState()
    m.mark_subquestion("Who is the CEO of Apex Holdings?")
    assert m.is_duplicate_subquestion("Who is the CEO of Apex Holdings?")
    assert not m.is_duplicate_subquestion("Who is the CEO")
    assert not m.is_duplicate_subquestion(
        "Who is the CEO of BrightLink Logistics?"
    )


def test_ceo_conclusion_rejects_unrelated_company_edge() -> None:
    """Multi-hop neighbor CEO of NovaTech must not answer BrightLink CEO."""
    foreign = Candidate(
        id="c1",
        source=CandidateSource.GRAPH_PATH,
        content="Marcus Chen -[CEO_OF]-> NovaTech Industries",
        score=1.0,
        structured={
            "head": "Marcus Chen",
            "relation": "CEO_OF",
            "tail": "NovaTech Industries",
            "query_entity": "BrightLink Logistics",
        },
    )
    out = extract_entity_conclusion(
        "Who is the CEO of BrightLink Logistics?", [foreign]
    )
    assert out is None


def test_work_conclusion_aggregates_employers() -> None:
    e1 = Candidate(
        id="w1",
        source=CandidateSource.GRAPH_PATH,
        content="Elena Varga -[WORKED_AT]-> Meridian Capital",
        score=1.0,
        structured={
            "head": "Elena Varga",
            "relation": "WORKED_AT",
            "tail": "Meridian Capital",
            "query_entity": "Elena Varga",
        },
    )
    e2 = Candidate(
        id="w2",
        source=CandidateSource.GRAPH_PATH,
        content="Elena Varga -[WORKED_AT]-> Orion Systems",
        score=1.0,
        structured={
            "head": "Elena Varga",
            "relation": "WORKED_AT",
            "tail": "Orion Systems",
            "query_entity": "Elena Varga",
        },
    )
    out = extract_entity_conclusion(
        "Which companies did Elena Varga previously work at?", [e1, e2]
    )
    assert out is not None
    assert "Meridian Capital" in out
    assert "Orion Systems" in out


def test_offline_brightlink_ceo_honest_no_answer() -> None:
    from agentic_graphrag.generation.offline_answer import offline_answer
    from agentic_graphrag.generation.trace import ReasoningChain, QueryStatus

    chain = ReasoningChain(question="Who is the CEO of BrightLink Logistics?")
    evidence = [
        Candidate(
            id="s1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="BrightLink Logistics -[SUPPLIES]-> Helix Compute (Company)",
            score=1.0,
            structured={
                "head": "BrightLink Logistics",
                "relation": "SUPPLIES",
                "tail": "Helix Compute",
                "query_entity": "BrightLink Logistics",
            },
        ),
        Candidate(
            id="c1",
            source=CandidateSource.GRAPH_NEIGHBOR,
            content="Marcus Chen -[CEO_OF]-> NovaTech Industries (Person)",
            score=0.2,
            structured={
                "head": "Marcus Chen",
                "relation": "CEO_OF",
                "tail": "NovaTech Industries",
                "query_entity": "BrightLink Logistics",
            },
        ),
    ]
    out = offline_answer(chain, evidence, conclusions="")
    assert out.status == QueryStatus.NO_ANSWER


def test_subsidiary_yes_offline() -> None:
    from agentic_graphrag.generation.offline_heuristics import focused_extract

    edges = [
        ("Apex Holdings", "PARENT_OF", "BrightLink Logistics"),
    ]
    ans = focused_extract(
        "Is BrightLink a subsidiary of Apex Holdings?",
        edges,
        [],
    )
    assert ans == "Yes"
