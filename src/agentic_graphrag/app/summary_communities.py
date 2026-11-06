"""Persist community projections and repair only affected connected components."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.config import runtime_dir
from agentic_graphrag.core.communities import Community
from agentic_graphrag.core.ports.communities import CommunityDetector
from agentic_graphrag.core.ports.stores import DocumentRecord
from agentic_graphrag.knowledge.communities import LeidenDetector
from agentic_graphrag.knowledge.community_incremental import update_communities
from agentic_graphrag.knowledge.community_projection import project_graph
from agentic_graphrag.stores.community_index import CommunityIndexStore
from agentic_graphrag.stores.scoped_graph_scan import entities_in_scope, relations_in_scope

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def communities_for(
    svc: QueryService, documents: list[DocumentRecord], *, detector: CommunityDetector | None = None
) -> tuple[tuple[Community, ...], str]:
    kb = svc.knowledge_base
    assert kb is not None and svc.knowledge_store is not None
    index = CommunityIndexStore(runtime_dir(svc.cfg) / "communities.sqlite3")
    try:
        return _communities(svc, documents, index=index, detector=detector)
    finally:
        index.close()


def _communities(
    svc: QueryService,
    documents: list[DocumentRecord],
    *,
    index: CommunityIndexStore,
    detector: CommunityDetector | None,
) -> tuple[tuple[Community, ...], str]:
    kb = svc.knowledge_base
    assert kb is not None and svc.knowledge_store is not None
    existing = index.get(kb.scope, kb.active_generation)
    changes = svc.knowledge_store.changes(kb.scope, limit=100000)
    change = next(
        (item for item in reversed(changes) if item.target_generation == kb.active_generation), None
    )
    previous_generation = change.source_generation if change else ""
    if existing is not None:
        return existing[1], previous_generation
    projection = project_graph(
        kb.scope,
        kb.active_generation,
        entities=list(entities_in_scope(svc.bundle.graph, kb.tenant_id)),
        relations=list(relations_in_scope(svc.bundle.graph, kb.tenant_id)),
        document_hashes={doc.doc_id: doc.content_sha256 for doc in documents},
    )
    detector = detector or LeidenDetector(resolution=svc.cfg.summaries.community_resolution)
    before = index.get(kb.scope, previous_generation) if previous_generation else None
    if before is not None and change is not None:
        update = update_communities(
            before[0],
            projection,
            previous=before[1],
            change=change,
            detector=detector,
            seed=svc.cfg.summaries.community_seed,
            full_rebuild_ratio=svc.cfg.summaries.incremental_full_rebuild_ratio,
        )
        communities = update.communities
    else:
        communities = detector.detect(projection, seed=svc.cfg.summaries.community_seed)
    index.save(projection, communities)
    return communities, previous_generation
