"""Stable document/community report inputs and dependency fingerprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agentic_graphrag.core.communities import Community
from agentic_graphrag.core.enums import PromptName
from agentic_graphrag.core.knowledge import KnowledgeScope, SummaryRecord
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.summarizer import prompt_version
from agentic_graphrag.knowledge.summary_inputs import SummaryInput, document_inputs


@dataclass(frozen=True)
class SummaryArtifact:
    record: SummaryRecord
    inputs: list[SummaryInput]
    context: str = ""


def document_artifact(
    doc: DocumentRecord, scope: KnowledgeScope, *, generation: str, model_revision: str
) -> SummaryArtifact:
    inputs = document_inputs(doc, scope)
    fingerprint = _fingerprint(inputs)
    record = SummaryRecord(
        summary_id="doc-" + hashlib.sha256(doc.doc_id.encode()).hexdigest()[:24],
        scope=scope,
        generation=generation,
        kind="document",
        document_ids=(doc.doc_id,),
        input_fingerprint=fingerprint,
        model_revision=model_revision,
        prompt_version=prompt_version(PromptName.DOCUMENT_SUMMARY),
    )
    return SummaryArtifact(record, inputs)


def community_artifact(
    community: Community, documents: dict[str, DocumentRecord], *, model_revision: str
) -> SummaryArtifact:
    inputs = [
        source
        for doc_id in community.document_ids
        if doc_id in documents
        for source in document_inputs(documents[doc_id], community.scope)
    ]
    fingerprint = hashlib.sha256(
        (community.input_fingerprint + _fingerprint(inputs)).encode()
    ).hexdigest()
    record = SummaryRecord(
        summary_id=community.id,
        scope=community.scope,
        generation=community.generation,
        kind="community",
        document_ids=community.document_ids,
        entity_ids=community.entity_ids,
        input_fingerprint=fingerprint,
        model_revision=model_revision,
        prompt_version=prompt_version(PromptName.COMMUNITY_SUMMARY),
    )
    return SummaryArtifact(record, inputs, context=json.dumps({"members": community.entity_ids}))


def _fingerprint(inputs: list[SummaryInput]) -> str:
    return hashlib.sha256(
        json.dumps(
            [[item.source.model_dump(), item.text] for item in inputs],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def reusable(before: SummaryRecord, after: SummaryRecord) -> bool:
    return before.status == "ready" and (
        before.summary_id,
        before.input_fingerprint,
        before.model_revision,
        before.prompt_version,
    ) == (after.summary_id, after.input_fingerprint, after.model_revision, after.prompt_version)
