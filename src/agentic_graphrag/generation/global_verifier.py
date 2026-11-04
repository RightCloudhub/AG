"""Verify global claims against authorized original versions, never summary text."""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field

from agentic_graphrag.agent.global_findings import MappedFinding
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import MessageRole, PromptName
from agentic_graphrag.core.evidence import Candidate, CandidateSource, Citation
from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.llm import LLMClient, Message, Tier
from agentic_graphrag.core.ports.stores import DocStore, DocumentRecord
from agentic_graphrag.knowledge.tokenizer import get_tokenizer
from agentic_graphrag.llm.roles import for_role
from agentic_graphrag.llm.structured import complete_structured

_PREVALENCE = re.compile(
    r"多数(?:合同|文档)|所有合同|所有文档|全部合同|\b(?:most|all) (?:contracts|documents)\b"
    r"|\d+\s*%\s*(?:of\s+)?(?:the\s+)?(?:contracts|documents)"
    r"|(?:百分之[\d一二三四五六七八九十百零点]+|\d+\s*%)(?:的)?(?:合同|文档)",
    re.I,
)


class GlobalVerdict(BaseModel):
    supported: bool = False
    reason: str = Field(default="", max_length=1000)


def original_excerpt(source: SourceRef, docs: DocStore, *, scope: KnowledgeScope) -> str:
    doc = _original_document(source, docs, scope=scope)
    chunk = next((chunk for chunk in doc.chunks if chunk.chunk_id == source.chunk_id), None)
    if chunk is None:
        raise ValueError("Original source chunk is unavailable")
    text = doc.content if source.span_origin == "document" else chunk.text
    end = source.span_end if source.span_end is not None else len(text)
    if source.span_start >= end or end > len(text):
        raise ValueError("Original source span is invalid")
    return text[source.span_start : end]


def _original_document(
    source: SourceRef, docs: DocStore, *, scope: KnowledgeScope
) -> DocumentRecord:
    if source.scope != scope:
        raise ValueError("Source belongs to another knowledge base")
    doc = docs.get(source.doc_id, tenant_id=scope.tenant_id)
    if doc is None or doc.version != source.doc_version or doc.metadata.get("lifecycle"):
        raise ValueError("Original source version is unavailable")
    if hashlib.sha256(doc.content.encode()).hexdigest() != source.content_sha256:
        raise ValueError("Original source hash changed")
    return doc


def verify_finding(
    finding: MappedFinding,
    docs: DocStore,
    *,
    scope: KnowledgeScope,
    llm: LLMClient,
    token_limit: int = 4000,
) -> list[Candidate]:
    # A summary occurrence count cannot establish whole-document clause prevalence.
    if not finding.sources or _PREVALENCE.search(finding.text):
        return []
    excerpts = [(source, original_excerpt(source, docs, scope=scope)) for source in finding.sources]
    if get_tokenizer("cl100k_base").count(json.dumps([text for _, text in excerpts])) > token_limit:
        return []
    result = complete_structured(
        for_role(llm, ModelRole.VERIFIER),
        [
            Message(role=MessageRole.SYSTEM, content=load_prompt(PromptName.GLOBAL_VERIFY)),
            Message(
                role=MessageRole.USER,
                content=json.dumps(
                    {"finding": finding.text, "originals": [text for _, text in excerpts]},
                    ensure_ascii=False,
                ),
            ),
        ],
        GlobalVerdict,
        tier=Tier.LIGHT,
    )
    if not result.supported:
        return []
    return [_candidate(source, text) for source, text in excerpts]


def _candidate(source: SourceRef, text: str) -> Candidate:
    key = "source-" + hashlib.sha256(source.model_dump_json().encode()).hexdigest()[:24]
    return Candidate(
        id=key,
        source=CandidateSource.FULLTEXT_CHUNK,
        content=text,
        score=1.0,
        citations=[
            Citation(
                doc_id=source.doc_id,
                chunk_id=source.chunk_id,
                doc_version=source.doc_version,
                content_sha256=source.content_sha256,
                span=text,
            )
        ],
        metadata={"source_refs": [source.model_dump(mode="json")]},
    )
