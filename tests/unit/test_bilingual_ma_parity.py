"""Bilingual multi-hop ownership / HQ questions should answer consistently."""

from __future__ import annotations

from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.api.schemas import QueryRequest
from agentic_graphrag.api.service import QueryService
from agentic_graphrag.generation.offline_heuristics import focused_extract
from agentic_graphrag.retrieval.graph_relation import relation_relevance

EN_Q = (
    "Who is the CEO of the company that acquired NovaTech, "
    "and in which city is NovaTech headquartered?"
)
CN_Q = "Apex Holdings 的 CEO 所在公司，后来收购了哪家公司？那家被收购公司的总部在哪个城市"

_EDGES = [
    ("Apex Holdings", "PARENT_OF", "NovaTech Industries"),
    ("Apex Holdings", "PARENT_OF", "BrightLink Logistics"),
    ("Elena Varga", "CEO_OF", "Apex Holdings"),
    ("Marcus Chen", "CEO_OF", "NovaTech Industries"),
]
_TEXTS = [
    "Apex Holdings -[PARENT_OF]-> NovaTech Industries (Company)",
    "Elena Varga -[CEO_OF]-> Apex Holdings (Company)",
    "NovaTech Industries is a technology company and a subsidiary of Apex Holdings. "
    "NovaTech Industries is headquartered in Austin and focuses on enterprise computing.",
]


def test_entity_expand_novatech_prefix():
    mentions = extract_entity_mentions(EN_Q)
    assert "NovaTech Industries" in mentions
    assert "Who" not in mentions


def test_cjk_question_words_not_entities():
    mentions = extract_entity_mentions(CN_Q)
    assert mentions == ["Apex Holdings"]


def test_chinese_relation_cues_score_parent_of():
    assert relation_relevance("PARENT_OF", "后来收购了哪家公司") >= 0.45
    assert relation_relevance("LOCATED_IN", "总部在哪个城市") >= 0.45


def test_focused_extract_en_and_cn_share_core_facts():
    en = focused_extract(EN_Q, _EDGES, _TEXTS)
    cn = focused_extract(CN_Q, _EDGES, _TEXTS)
    assert en is not None and cn is not None
    for ans in (en, cn):
        assert "Elena Varga" in ans
        assert "Apex Holdings" in ans
        assert "NovaTech" in ans
        assert "Austin" in ans


def test_offline_service_bilingual_parity():
    svc = QueryService.create_offline()
    try:
        en = svc.run_query(
            QueryRequest(question=EN_Q, force_agentic=True), tenant_id="t", user_id="u"
        )
        cn = svc.run_query(
            QueryRequest(question=CN_Q, force_agentic=True), tenant_id="t", user_id="u"
        )
    finally:
        svc.close()
    assert en.status == "answered"
    assert cn.status == "answered"
    for ans in (en.answer, cn.answer):
        assert "Elena Varga" in ans
        assert "NovaTech" in ans
        assert "Austin" in ans
    # CN must not invent pilot-only subsidiaries when seed graph is used.
    assert "Delta Fabrication" not in (cn.answer or "")
