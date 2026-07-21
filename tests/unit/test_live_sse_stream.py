"""True incremental SSE: hop events before final answer (P3-PERF-06)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_graphrag.api.app import create_app
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.retrieval.graph_beam import (
    BeamConfig,
    BeamExpander,
    blend_relation_score,
    edge_score,
)
from agentic_graphrag.stores.interfaces import RelationRecord
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore


def test_stream_emits_hops_before_answer_multihop() -> None:
    svc = QueryService.create_offline()
    req = QueryRequest(
        question="Who is the CEO of the parent company of BrightLink Logistics?",
        force_agentic=True,
    )
    events = list(svc.stream_query_events(req, tenant_id="t", user_id="u"))
    types = [e for e, _ in events]
    assert types[0] == "triage"  # synthetic force_agentic triage for contract stability
    assert events[0][1].get("force_agentic") is True or events[0][1].get("rule_hit") == (
        "force_agentic"
    )
    assert "answer" in types
    assert types[-1] == "answer"
    progress = types[: types.index("answer")]
    assert progress.count("sub_question") >= 2
    assert progress.count("hop_done") >= 2
    assert progress.index("sub_question") < progress.index("hop_done")
    # First hop before second hop
    first_sq = progress.index("sub_question")
    second_sq = progress.index("sub_question", first_sq + 1)
    assert second_sq > first_sq
    svc.close()


def test_stream_triage_then_progress() -> None:
    svc = QueryService.create_offline()
    req = QueryRequest(question="Who is the CEO of Apex Holdings?")
    events = list(svc.stream_query_events(req, tenant_id="t", user_id="u"))
    types = [e for e, _ in events]
    assert types[0] == "triage"
    assert "answer" in types
    svc.close()


def test_http_stream_sse_frames() -> None:
    svc = QueryService.create_offline()
    app = create_app(query_service=svc)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/query/stream",
        json={
            "question": "Who is the CEO of the parent company of NovaTech Industries?",
            "force_agentic": True,
        },
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "event: triage" in text
    assert "event: sub_question" in text
    assert "event: answer" in text
    ans_at = text.find("event: answer")
    assert ans_at > 0
    prefix = text[:ans_at]
    assert "event: sub_question" in prefix or "event: hop_done" in prefix
    svc.close()


def test_blend_relation_score_embedding_hook() -> None:
    assert blend_relation_score(0.5, None) == 0.5
    blended = blend_relation_score(0.2, 1.0, embed_weight=0.5)
    assert 0.5 < blended <= 1.0
    rel = RelationRecord(
        id="r1",
        type="CEO_OF",
        head_id="h",
        tail_id="t",
        confidence=1.0,
    )
    base = edge_score(rel, "unrelated gibberish xyz")
    boosted = edge_score(rel, "unrelated gibberish xyz", embed_sim=0.95)
    assert boosted > base


def test_beam_layer_edges_uses_relation_embed_sim() -> None:
    """Production beam path must call relation_embed_sim when configured."""
    from agentic_graphrag.stores.interfaces import EntityRecord

    store = InMemoryGraphStore()
    store.upsert_entities(
        [
            EntityRecord(id="a", name="Apex", type="Company"),
            EntityRecord(id="e", name="Elena", type="Person"),
        ]
    )
    store.upsert_relations(
        [
            RelationRecord(
                id="r1",
                type="CEO_OF",
                head_id="e",
                tail_id="a",
                head_name="Elena",
                tail_name="Apex",
                confidence=1.0,
            )
        ]
    )
    called: list[tuple[str, str | None]] = []

    def scorer(rel_type: str, sub_q: str | None) -> float:
        called.append((rel_type, sub_q))
        return 0.99 if rel_type.upper() == "CEO_OF" else 0.0

    expander = BeamExpander(
        store,
        BeamConfig(relation_embed_sim=scorer, max_neighbors_per_layer=10, beam_width=5),
    )
    rows = expander.layer_edges("Apex", preferred_relations=None, sub_question="who is ceo")
    assert called, "relation_embed_sim must be invoked from layer_edges"
    assert any(r[0].upper() == "CEO_OF" for r in called)
    assert rows  # scored edges returned
