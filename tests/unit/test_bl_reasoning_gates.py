"""BL-12 parallel DAG branches and BL-13 relation / entailment gates."""

from __future__ import annotations

import json
import threading
import time

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop_runtime import AgentRuntime
from agentic_graphrag.agent.parallel_branches import (
    BranchRun,
    is_prefetched,
    ready_siblings,
    retrieve_group,
)
from agentic_graphrag.eval.metrics_evidence import _Catalog, _CatalogEntry, _claim_grounded
from agentic_graphrag.generation.answer import AnswerPayload, _apply_payload
from agentic_graphrag.generation.citations import validate_answered_claims
from agentic_graphrag.generation.entailment import JUDGE_ABSTAINED, NOT_ENTAILED, judge_claims
from agentic_graphrag.generation.relation_consistency import claim_relations, relation_consistent
from agentic_graphrag.generation.trace import Claim, QueryStatus, ReasoningChain
from agentic_graphrag.retrieval.contracts import Candidate, CandidateSource

# --- BL-12 ---------------------------------------------------------------


def _sq(sid: str, text: str, deps: list[str] | None = None) -> dict:
    return {"id": sid, "text": text, "depends_on": deps or []}


PLAN = [
    _sq("sq1", "Who is the CEO of Apex Holdings?"),
    _sq("sq2", "Who is the CEO of Helix Compute?"),
    _sq("sq3", "Where did {from:sq1} and {from:sq2} work?", ["sq1", "sq2"]),
]


def test_ready_siblings_only_returns_independent_ready_nodes() -> None:
    got = ready_siblings(PLAN, 0, done_ids=set(), limit=5)
    assert [s.id for s in got] == ["sq2"]
    assert ready_siblings(PLAN, 0, done_ids=set(), limit=0) == []


class _SlowExecutor:
    known_entities: list[str] = []

    def __init__(self) -> None:
        self.threads: set[str] = set()

    def run(self, text, *, entities_hint=None, allow_llm=True, tenant_id=None):
        self.threads.add(threading.current_thread().name)
        time.sleep(0.2)
        cand = Candidate(id=f"c:{text}", source=CandidateSource.FULLTEXT, content=text, score=1)
        return [cand], []


def test_branches_run_concurrently() -> None:
    ex = _SlowExecutor()
    run = BranchRun(hints_for=lambda _t: [], allow_llm=False, tenant_id=None)
    t0 = time.perf_counter()
    out = retrieve_group(ex, [("a", "A"), ("b", "B"), ("c", "C")], run)
    assert time.perf_counter() - t0 < 0.5  # max(branch), not Σ(branch)=0.6
    assert set(out) == {"a", "b", "c"} and len(ex.threads) == 3


def test_parallel_sibling_shares_the_hop() -> None:
    rt = AgentRuntime(_SlowExecutor(), None, GuardrailConfig(max_hops=4))
    chain = ReasoningChain(question="q", route="agentic")
    state = {
        "question": "q",
        "chain": chain.model_dump(),
        "sub_questions": PLAN,
        "current_index": 0,
        "allow_llm": False,
    }
    first = rt.node_executor(state)
    assert first["hop"] == 1 and is_prefetched(first, "sq2")
    rt.memory.mark_subquestion_done("sq1", "Elena Varga")
    second = rt.node_executor({**first, "current_index": 1})
    assert second["hop"] == 1  # breadth no longer consumes depth
    assert not is_prefetched(second, "sq2")
    meta = ReasoningChain.model_validate(first["chain"]).metadata
    assert meta["parallel_branches"] == [["sq1", "sq2"]]


# --- BL-13 ---------------------------------------------------------------


def _edge(cid: str, head: str, rel: str, tail: str) -> Candidate:
    return Candidate(
        id=cid,
        source=CandidateSource.GRAPH_NEIGHBOR,
        content=f"{head} -[{rel}]-> {tail} (Company)",
        score=1.0,
        structured={"kind": "neighbor", "query_entity": tail, "neighbor": head},
    )


def test_relation_cues_are_bilingual() -> None:
    assert "CEO_OF" in claim_relations("苹果控股的首席执行官是埃琳娜")
    assert "FOUNDED" in claim_relations("Lee Min-jun founded Harbor Components")
    assert relation_consistent("X is the CEO of Y", "X -[CEO_OF]-> Y")
    assert relation_consistent("X owns Y", "Y -[SUBSIDIARY_OF]-> X")  # compatible inverse
    assert relation_consistent("X is notable", "X -[FOUNDED]-> Y")  # no cue: not judged


def test_gate_rejects_claim_citing_edge_with_another_relation() -> None:
    ev = [_edge("e1", "Elena Varga", "WORKED_AT", "Apex Holdings")]
    bad = [Claim(text="Elena Varga is the CEO of Apex Holdings", evidence_ids=["e1"])]
    good = [Claim(text="Elena Varga worked at Apex Holdings", evidence_ids=["e1"])]
    assert validate_answered_claims(bad, ev) == "claim text not supported by cited evidence content"
    assert validate_answered_claims(good, ev) is None


def test_eval_fabrication_uses_same_relation_rule() -> None:
    catalog: _Catalog = {"e1": _CatalogEntry(content="Elena Varga -[WORKED_AT]-> Apex Holdings")}
    claim = {"text": "Elena Varga is the CEO of Apex Holdings", "evidence_ids": ["e1"]}
    assert _claim_grounded(claim, catalog) is False


class _JudgeLLM:
    supports_entailment = True

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, messages, *, tier=None, temperature=None, response_format=None) -> str:
        self.calls += 1
        return self.reply


def test_entailment_judge_rejects_and_abstains() -> None:
    ev = [_edge("e1", "Elena Varga", "CEO_OF", "Apex Holdings")]
    claims = [Claim(text="Elena Varga is the CEO of Apex Holdings", evidence_ids=["e1"])]
    no = _JudgeLLM(json.dumps({"verdicts": [{"index": 0, "label": "contradicted"}]}))
    reason, report = judge_claims(claims, ev, no)
    assert reason == NOT_ENTAILED and report["entailment"] == {"0": "contradicted"}
    yes = _JudgeLLM(json.dumps({"verdicts": [{"index": 0, "label": "entailed"}]}))
    assert judge_claims(claims, ev, yes)[0] == ""
    broken = _JudgeLLM("not json")
    reason, report = judge_claims(claims, ev, broken)
    assert reason == "" and JUDGE_ABSTAINED in report


def test_answer_payload_rejected_when_judge_disagrees() -> None:
    ev = [_edge("e1", "Elena Varga", "CEO_OF", "Apex Holdings")]
    payload = AnswerPayload(
        answer="Elena Varga",
        status=QueryStatus.ANSWERED,
        claims=[Claim(text="Elena Varga is the CEO of Apex Holdings", evidence_ids=["e1"])],
    )
    judge = _JudgeLLM(json.dumps({"verdicts": [{"index": 0, "label": "not_enough_info"}]}))
    chain, reason = _apply_payload(ReasoningChain(question="q"), payload, ev, llm=judge)
    assert chain is None and reason == NOT_ENTAILED
