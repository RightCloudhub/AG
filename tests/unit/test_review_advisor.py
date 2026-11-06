"""Shadow recommendations and calibration gates never manufacture human approval."""

import json

import pytest

from agentic_graphrag.config_review import ReviewAutomationConfig
from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.ports.stores import ChunkRecord, DocumentRecord
from agentic_graphrag.core.review_advice import ReviewCalibration, ReviewPair
from agentic_graphrag.knowledge.review.advisor import ReviewAdvisor
from agentic_graphrag.knowledge.review.calibration import (
    apply_if_allowed,
    automation_blocker,
    calibrate,
)
from agentic_graphrag.knowledge.review.queue import ReviewQueue
from agentic_graphrag.stores.doc_store import InMemoryDocStore
from agentic_graphrag.stores.review_advice import SQLiteReviewAdviceStore


class AdvisorModel:
    def __init__(self, decision="approve"):
        self.decision = decision
        self.inputs = []

    def complete(self, messages, **kwargs):
        self.inputs.append(messages)
        return json.dumps(
            {"decision": self.decision, "reason": "Original text supports the relation"}
        )


def setup_advisor(tmp_path):
    kb = KnowledgeBase(tenant_id="t", kb_id="contracts")
    queue = ReviewQueue(tmp_path / "review.jsonl")
    item = queue.enqueue(
        "extraction",
        {
            "staged": True,
            "kb_id": kb.kb_id,
            "triple": {
                "relation": "LOW_RISK",
                "source_doc_id": "d",
                "source_doc_version": 1,
                "source_chunk_id": "c",
            },
        },
        tenant_id="t",
        item_id="fixed-item",
    )
    docs = InMemoryDocStore()
    chunk = ChunkRecord(
        chunk_id="c", doc_id="d", text="Alice works for Apex.", index=0, tenant_id="t"
    )
    docs.save(
        DocumentRecord(
            doc_id="d", title="D", content=chunk.text, chunks=[chunk], tenant_id="t", kb_id=kb.kb_id
        )
    )
    store = SQLiteReviewAdviceStore(tmp_path / "advice.sqlite3")
    proposer, verifier = AdvisorModel(), AdvisorModel()
    advisor = ReviewAdvisor(
        knowledge_base=kb,
        proposer=proposer,
        verifier=verifier,
        proposer_revision="proposer:1",
        verifier_revision="verifier:1",
        store=store,
    )
    return kb, queue, item, docs, store, advisor


def calibration(kb, *, real=False, held_out=False, incorrect=0):
    baseline = ReviewCalibration(
        scope=kb.scope,
        dataset_id="fixture",
        proposer_revision="proposer:1",
        verifier_revision="verifier:1",
        real_corpus=real,
        held_out=held_out,
        reviewed=0,
        approved=0,
        false_approvals=0,
        false_approval_upper=1,
    )
    pairs = [
        ReviewPair(
            review_id=str(i),
            agent_decision="approve",
            human_decision="reject" if i < incorrect else "approve",
        )
        for i in range(500)
    ]
    return calibrate(pairs, baseline)


def test_shadow_runs_independent_models_and_keeps_item_pending(tmp_path):
    kb, queue, item, docs, store, advisor = setup_advisor(tmp_path)
    advice = advisor.advise(item, docs)
    assert advice.verified and advice.decision == "approve"
    assert queue.get(item.id).status == "pending"
    assert store.get(kb.scope, item.id) == advice
    assert store.get(KnowledgeScope(tenant_id="other", kb_id=kb.kb_id), item.id) is None
    assert len(advisor.proposer.inputs) == len(advisor.verifier.inputs) == 1
    assert advisor.proposer.inputs[0][1].content == advisor.verifier.inputs[0][1].content
    assert (
        automation_blocker(
            item, advice, knowledge_base=kb, calibration=None, policy=ReviewAutomationConfig()
        )
        == "automation_disabled"
    )
    store.close()


def test_fake_corpus_and_bad_false_acceptance_cannot_unlock_automation(tmp_path):
    kb, _queue, item, docs, store, advisor = setup_advisor(tmp_path)
    advice = advisor.advise(item, docs)
    policy = ReviewAutomationConfig(
        enabled=True, relation_allowlist=("LOW_RISK",), sample_rate=0.000001
    )
    assert (
        automation_blocker(
            item, advice, knowledge_base=kb, calibration=calibration(kb), policy=policy
        )
        == "real_held_out_calibration_required"
    )
    bad = calibration(kb, real=True, held_out=True, incorrect=20)
    assert (
        automation_blocker(item, advice, knowledge_base=kb, calibration=bad, policy=policy)
        == "false_approval_bound_exceeds_policy"
    )
    good = calibration(kb, real=True, held_out=True)
    calls = []
    assert (
        apply_if_allowed(
            item,
            advice,
            knowledge_base=kb,
            calibration=good,
            policy=policy,
            approve=lambda current, evidence: calls.append(current.id),
        )
        == "approved_with_audit"
    )
    assert calls == [item.id]
    store.save_calibration(good)
    assert store.get_calibration(kb.scope) == good
    store.close()


def test_sampling_changed_input_and_model_revisions_keep_human_review(tmp_path):
    kb, _queue, item, docs, store, advisor = setup_advisor(tmp_path)
    advice = advisor.advise(item, docs)
    good = calibration(kb, real=True, held_out=True)
    policy = ReviewAutomationConfig(enabled=True, relation_allowlist=("LOW_RISK",), sample_rate=1)
    assert (
        automation_blocker(item, advice, knowledge_base=kb, calibration=good, policy=policy)
        == "selected_for_human_sample"
    )
    changed = advice.model_copy(update={"proposer_revision": "new-model"})
    assert (
        automation_blocker(item, changed, knowledge_base=kb, calibration=good, policy=policy)
        == "calibration_model_or_scope_mismatch"
    )
    item.payload["modified"] = True
    assert (
        automation_blocker(item, advice, knowledge_base=kb, calibration=good, policy=policy)
        == "advice_not_verified_for_current_input"
    )
    store.close()


def test_duplicate_pairs_are_rejected_and_no_approval_has_maximum_uncertainty(tmp_path):
    kb, _queue, _item, _docs, store, _advisor = setup_advisor(tmp_path)
    pair = ReviewPair(review_id="same", human_decision="approve", agent_decision="approve")
    with pytest.raises(ValueError, match="inflate"):
        calibrate([pair, pair], calibration(kb))
    assert calibrate([], calibration(kb)).false_approval_upper == 1
    store.close()


def test_deleted_sources_and_other_knowledge_bases_cannot_receive_advice(tmp_path):
    kb, _queue, item, docs, store, advisor = setup_advisor(tmp_path)
    item.payload["kb_id"] = "other"
    with pytest.raises(ValueError, match="another knowledge"):
        advisor.advise(item, docs)
    item.payload["kb_id"] = kb.kb_id
    docs.delete("d", tenant_id="t")
    with pytest.raises(ValueError, match="deleted"):
        advisor.advise(item, docs)
    store.close()
