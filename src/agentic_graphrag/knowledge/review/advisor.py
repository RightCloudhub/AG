"""Independent source-grounded suggestions are persisted without deciding the queue item."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import MessageRole, PromptName
from agentic_graphrag.core.knowledge import KnowledgeBase
from agentic_graphrag.core.ports.llm import CompletionClient, Message
from agentic_graphrag.core.ports.stores import DocStore
from agentic_graphrag.core.review_advice import (
    ReviewAdvice,
    ReviewAdviceStore,
    ReviewRecommendation,
)
from agentic_graphrag.knowledge.review.document_guard import stale_document
from agentic_graphrag.knowledge.review.queue import ReviewItem
from agentic_graphrag.llm.structured import complete_structured

MAX_REVIEW_SOURCE_CHARS = 20_000


@dataclass
class ReviewAdvisor:
    knowledge_base: KnowledgeBase
    proposer: CompletionClient
    verifier: CompletionClient
    proposer_revision: str
    verifier_revision: str
    store: ReviewAdviceStore

    def advise(self, item: ReviewItem, docs: DocStore) -> ReviewAdvice:
        if (item.tenant_id or "default") != self.knowledge_base.tenant_id:
            raise ValueError("Review item belongs to another tenant")
        if item.payload.get("kb_id", "default") != self.knowledge_base.kb_id:
            raise ValueError("Review item belongs to another knowledge base")
        if stale_document(item, docs):
            raise ValueError("Review item refers to a deleted or replaced source")
        evidence = _source_text(item, docs)
        context = {"candidate": item.payload, "original_sources": evidence}
        proposed = _recommend(self.proposer, context)
        verified = _recommend(self.verifier, context)
        advice = ReviewAdvice(
            review_id=item.id,
            scope=self.knowledge_base.scope,
            generation=self.knowledge_base.active_generation,
            proposer_revision=self.proposer_revision,
            verifier_revision=self.verifier_revision,
            decision=proposed.decision,
            reason=proposed.reason,
            verified=(verified.decision == proposed.decision and proposed.decision != "abstain"),
            input_fingerprint=review_fingerprint(item),
        )
        self.store.save(advice)
        return advice


def review_fingerprint(item: ReviewItem) -> str:
    return hashlib.sha256(json.dumps(item.payload, sort_keys=True).encode()).hexdigest()


def _recommend(llm: CompletionClient, context: dict[str, Any]) -> ReviewRecommendation:
    messages = [
        Message(role=MessageRole.SYSTEM, content=load_prompt(PromptName.REVIEW_ADVICE)),
        Message(role=MessageRole.USER, content=json.dumps(context, ensure_ascii=False)),
    ]
    return complete_structured(llm, messages, ReviewRecommendation, max_retries=1)


def _source_text(item: ReviewItem, docs: DocStore) -> list[dict[str, Any]]:
    sources = _review_sources(item)
    result = [
        _review_excerpt(source, docs=docs, tenant_id=item.tenant_id or "default")
        for source in sources
    ]
    if not result:
        raise ValueError("Review advice requires original source evidence")
    return result


def _review_sources(item: ReviewItem) -> list[dict[str, Any]]:
    sources = list(item.payload.get("source_refs", []))
    triple = item.payload.get("triple") or item.payload.get("incoming") or {}
    if triple.get("source_doc_id"):
        sources.append(
            {"doc_id": triple["source_doc_id"], "chunk_id": triple.get("source_chunk_id")}
        )
    return sources


def _review_excerpt(source: dict[str, Any], *, docs: DocStore, tenant_id: str) -> dict[str, Any]:
    doc = docs.get(str(source.get("doc_id", "")), tenant_id=tenant_id)
    if doc is None:
        raise ValueError("Review source is unavailable")
    chunk = next((c for c in doc.chunks if c.chunk_id == source.get("chunk_id")), None)
    if chunk is None or len(chunk.text) > MAX_REVIEW_SOURCE_CHARS:
        raise ValueError("Review needs a bounded original source chunk")
    return {"doc_id": doc.doc_id, "version": doc.version, "text": chunk.text}
