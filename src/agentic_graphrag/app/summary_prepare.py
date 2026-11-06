"""Select immutable summary inputs and fingerprint the actual writer configuration."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.app.query_selection import select_documents
from agentic_graphrag.app.summary_communities import communities_for
from agentic_graphrag.core.knowledge import SummaryRecord
from agentic_graphrag.core.knowledge_versions import KnowledgeSnapshot
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.communities import CommunityDetector
from agentic_graphrag.core.ports.knowledge import SummaryStore
from agentic_graphrag.knowledge.summary_artifacts import (
    SummaryArtifact,
    community_artifact,
    document_artifact,
)
from agentic_graphrag.stores.snapshot_bundle import capture_snapshot

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def summary_service(svc: QueryService, *, has_client: bool) -> QueryService:
    kb = svc.knowledge_base
    assert kb is not None and svc.knowledge_store is not None
    require_summary_capability(svc, has_client=has_client)
    if svc.knowledge_store.get_snapshot(kb.scope, kb.active_generation) is None:
        capture_snapshot(
            svc.bundle,
            KnowledgeSnapshot(
                scope=kb.scope,
                generation=kb.active_generation,
                graph_version=kb.active_generation,
                vector_version=kb.active_generation,
                knowledge_base=kb,
            ),
            store=svc.knowledge_store,
        )
    return select_documents(svc, None, tenant_id=kb.tenant_id)


def require_summary_capability(svc: QueryService, *, has_client: bool = False) -> None:
    kb = svc.knowledge_base
    if kb is None or not kb.global_summary_enabled or not (svc.allow_llm or has_client):
        raise ApiError(
            "CAPABILITY_UNAVAILABLE",
            "Summary generation requires enabled model capability",
            status_code=503,
        )


def writer_revision(svc: QueryService) -> str:
    target_id = svc.cfg.models.roles.get(ModelRole.WRITER)
    if target_id:
        target = svc.cfg.models.catalog[target_id]
        config: object = [
            target.model_dump(mode="json"),
            svc.cfg.models.providers[target.provider].model_dump(mode="json"),
        ]
    else:
        config = [svc.cfg.llm.strong_model, svc.settings.llm_base_url]
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def summary_artifacts(
    svc: QueryService, *, detector: CommunityDetector | None
) -> tuple[list[SummaryArtifact], list[SummaryRecord]]:
    kb = svc.knowledge_base
    assert kb is not None and svc.knowledge_store is not None
    docs = [
        doc
        for key in svc.bundle.docs.list_ids(tenant_id=kb.tenant_id)
        if (doc := svc.bundle.docs.get(key, tenant_id=kb.tenant_id)) is not None
    ]
    communities, previous_generation = communities_for(svc, docs, detector=detector)
    model = writer_revision(svc)
    artifacts = [
        document_artifact(doc, kb.scope, generation=kb.active_generation, model_revision=model)
        for doc in docs
    ]
    documents = {doc.doc_id: doc for doc in docs}
    artifacts.extend(
        community_artifact(group, documents, model_revision=model) for group in communities
    )
    prior = (
        cast(SummaryStore, svc.knowledge_store).summaries(kb.scope, generation=previous_generation)
        if previous_generation
        else []
    )
    return artifacts, prior
