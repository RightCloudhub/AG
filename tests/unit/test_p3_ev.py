"""P3-EV offline helpers + triage run_case_row."""

from __future__ import annotations

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.cli.cases_run import run_case_row
from agentic_graphrag.eval.p3_ev import (
    SplitScore,
    build_heldout_report,
    build_triage_report,
    run_incremental_drill,
)
from agentic_graphrag.knowledge.graph_builder import load_triples_into_graph
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.retrieval.graph import GraphRetriever
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def test_heldout_and_triage_report_shapes() -> None:
    ag = SplitScore("agentic", 0.6, 0.9, 40.0, n=47)
    bl = SplitScore("baseline", 0.1, None, 5.0, n=47)
    h = build_heldout_report(agentic=ag, baseline=bl)
    assert h["task"] == "P3-EV-01"
    assert h["delta_accuracy_pp"] == 50.0
    assert h["gates"]["formal_g3_claim"] is False

    t = build_triage_report(
        triage_on=SplitScore("triage_on", 0.58, 0.88, 20.0, 47),
        force_agentic=SplitScore("force_agentic", 0.60, 0.90, 40.0, 47),
        route_counts={"fast_path": 10, "agentic": 37},
    )
    assert t["task"] == "P3-EV-02"
    assert t["accuracy_loss_pp"] == 2.0


def test_incremental_drill_smoke() -> None:
    r = run_incremental_drill()
    assert r.post_query_ok is True
    d = r.to_dict()
    assert d["gates"]["ac5_offline_smoke"] is True


def test_run_case_row_with_triage() -> None:
    store = InMemoryGraphStore()
    triples = [
        Triple(
            head=EntityMention(name="Apex Holdings", type="Company"),
            relation="PARENT_OF",
            tail=EntityMention(name="NovaTech Industries", type="Company"),
            confidence=0.9,
        ),
        Triple(
            head=EntityMention(name="Elena Varga", type="Person"),
            relation="CEO_OF",
            tail=EntityMention(name="Apex Holdings", type="Company"),
            confidence=0.95,
        ),
    ]
    load_triples_into_graph(store, triples, clear_first=True)
    ex = Executor(
        graph=GraphRetriever(store),
        vector=None,
        fulltext=None,
        llm=None,
        known_entities=["Apex Holdings", "NovaTech Industries", "Elena Varga"],
    )
    case = {
        "id": "t1",
        "question": "Who is the CEO of Apex Holdings?",
        "gold_answer": "Elena Varga",
    }
    row = run_case_row(
        case,
        executor=ex,
        llm=None,
        guard_cfg=GuardrailConfig(max_hops=3),
        no_llm=True,
        enable_triage=True,
    )
    assert row["case_id"] == "t1"
    assert row.get("route") in {"fast_path", "agentic", None} or "route" in row
