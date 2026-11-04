"""Public service composition carries dual-mode writes through review and publication."""

import json

import pytest

from agentic_graphrag.app.documents import lifecycle_for
from agentic_graphrag.app.knowledge_scope import knowledge_store, select_service
from agentic_graphrag.app.knowledge_writes import prepare_upload
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.review_app import ReviewApp
from agentic_graphrag.app.write_path import build_inline_worker
from agentic_graphrag.config import AppConfig, Settings
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.llm.provider import MockLLMProvider


@pytest.fixture
def root_service(tmp_path, monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_DIR", str(tmp_path / "runtime"))
    cfg = AppConfig()
    cfg.paths.cache_dir = str(tmp_path / "cache")
    svc = QueryService.create_offline(cfg=cfg, settings=Settings(), seed_triples=tmp_path / "empty")
    yield svc
    svc.close()


def upload(svc, kb, *, operation="upload", text="Alice Stone is the CEO of Apex Holdings."):
    bound = select_service(svc, tenant_id=kb.tenant_id, kb_id=kb.kb_id)
    shadow = prepare_upload(bound, operation)
    row = lifecycle_for(shadow).accept(
        DocumentRecord("", "contract.txt", text, tenant_id=kb.tenant_id)
    )
    shadow.ingest_tasks.create(kb.tenant_id, [row], task_id=operation)
    return shadow, row


def test_required_review_publishes_shadow_only_after_human_decision(root_service):
    svc = root_service
    kb = knowledge_store(svc).create(KnowledgeBase(kb_id="reviewed", review_policy="required"))
    shadow, row = upload(svc, kb)
    build_inline_worker(svc).run_once()
    assert shadow.ingest_tasks.get("upload").status == "review"
    active = select_service(svc, tenant_id="default", kb_id=kb.kb_id)
    assert active.bundle.graph.counts()["relations"] == 0
    assert active.bundle.docs.get(row["doc_id"], tenant_id="default") is None
    item = active.review_queue.list()[0]
    decided = ReviewApp(active).decide(item.id, "approve", tenant_id="default", reviewer="human")
    assert decided.execution["applied"]
    published = select_service(svc, tenant_id="default", kb_id=kb.kb_id)
    assert published.knowledge_base.active_generation == shadow.knowledge_base.active_generation
    assert published.bundle.graph.counts()["relations"] == 1
    assert published.ingest_tasks.get("upload").status == "done"


def test_open_worker_uses_free_labels_and_preserves_an_isolated_entity(root_service, monkeypatch):
    svc = root_service
    svc.allow_llm = True
    kb = knowledge_store(svc).create(
        KnowledgeBase(kb_id="open", build_mode="open", rule_planner_profile=None)
    )
    text = "小林负责星海项目。独立审计委员会尚未介入。"
    payload = {
        "entities": [
            {
                "ref": "a",
                "name": "小林",
                "entity_type": "项目负责人",
                "description": "星海项目负责人",
                "aliases": [],
                "evidence": {"quote": "小林"},
            },
            {
                "ref": "b",
                "name": "星海项目",
                "entity_type": "研发项目",
                "description": "小林负责的项目",
                "aliases": [],
                "evidence": {"quote": "星海项目"},
            },
            {
                "ref": "c",
                "name": "独立审计委员会",
                "entity_type": "委员会",
                "description": "尚未介入",
                "aliases": [],
                "evidence": {"quote": "独立审计委员会"},
            },
        ],
        "relations": [
            {
                "head": "a",
                "tail": "b",
                "predicate": "负责",
                "description": "小林负责星海项目",
                "confidence": 0.98,
                "evidence": {"quote": "小林负责星海项目"},
            }
        ],
    }
    monkeypatch.setattr(
        QueryService,
        "_build_llm",
        lambda self, budget: MockLLMProvider(
            {"Extract entities": json.dumps(payload, ensure_ascii=False)}, budget=budget
        ),
    )
    monkeypatch.setattr(
        "agentic_graphrag.app.write_path.batch_embedder",
        lambda *args, **kwargs: MockLLMProvider().embed_many,
    )
    shadow, row = upload(svc, kb, text=text)
    build_inline_worker(svc).run_once()
    assert shadow.ingest_tasks.get("upload").status == "done"
    published = select_service(svc, tenant_id="default", kb_id=kb.kb_id)
    assert published.bundle.graph.counts()["nodes"] == 3
    assert published.bundle.graph.counts()["relations"] == 1
    assert published.bundle.docs.get(row["doc_id"], tenant_id="default").metadata["index_complete"]
    relation = published.bundle.graph.scan_relations(tenant_id="default")[0]
    assert relation.type == "负责"
    assert relation.sources[0]["scope"]["kb_id"] == "open"
