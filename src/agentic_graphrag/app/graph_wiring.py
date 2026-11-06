"""Bind extraction, schemas, entity embeddings and review to one knowledge generation."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from agentic_graphrag.config import resolve_embedding_base_url, runtime_dir
from agentic_graphrag.core.models import ModelRole
from agentic_graphrag.core.ports.entities import EntitySemanticIndex
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.knowledge.graph_ingest import GraphIngestDeps, GraphIngestor
from agentic_graphrag.knowledge.schema_check import SchemaDefinition
from agentic_graphrag.knowledge.schema_registry import schema_for_knowledge_base
from agentic_graphrag.llm.roles import for_role
from agentic_graphrag.stores.entity_index import SQLiteEntityIndex
from agentic_graphrag.stores.schema_registry import SQLiteSchemaRegistry

if TYPE_CHECKING:
    from agentic_graphrag.app.query_service import QueryService


def entity_index_for(svc: QueryService) -> EntitySemanticIndex:
    root = svc.parent or svc
    with root._lock:
        if root.entity_index is None:
            root.entity_index = SQLiteEntityIndex(runtime_dir(svc.cfg) / "entities.sqlite3")
        return root.entity_index


def entity_space(svc: QueryService, *, generation: str | None = None) -> str:
    generation = generation or (svc.knowledge_base.active_generation if svc.knowledge_base else "1")
    identity = json.dumps(
        [svc.cfg.llm.embedding_model, resolve_embedding_base_url(svc.settings, svc.cfg)]
    )
    return f"{hashlib.sha256(identity.encode()).hexdigest()}:{generation}"


def schema_for_service(svc: QueryService) -> SchemaDefinition | None:
    kb = svc.knowledge_base
    if kb is None or kb.build_mode == "open" or kb.schema_id is None:
        return None
    registry = SQLiteSchemaRegistry(runtime_dir(svc.cfg) / "schemas.sqlite3")
    try:
        return schema_for_knowledge_base(kb, registry)
    finally:
        registry.close()


def graph_ingestor(svc: QueryService, *, llm: LLMClient | None = None) -> GraphIngestor:
    from agentic_graphrag.app.write_path import events_for

    llm = llm or (
        svc._build_llm(
            BudgetTracker(
                max_llm_calls=svc.cfg.indexing.max_llm_calls,
                max_tokens=svc.cfg.indexing.max_tokens,
            )
        )
        if svc.allow_llm
        else None
    )
    opened = bool(svc.knowledge_base and svc.knowledge_base.build_mode == "open")
    index = entity_index_for(svc) if opened else None
    if index is not None and llm is not None:
        from agentic_graphrag.app.entity_generations import seed_entities

        seed_entities(svc, index=index, embedder=llm)
    return GraphIngestor(
        GraphIngestDeps(
            graph=svc.bundle.graph,
            review_queue=svc.review_queue,
            llm=llm,
            events=events_for(svc),
            knowledge_base=svc.knowledge_base,
            schema=schema_for_service(svc),
            entity_index=index,
            embedding_space=entity_space(svc) if opened else "",
            embedder=llm,
            resolution_llm=for_role(llm, ModelRole.VERIFIER) if llm is not None else None,
        )
    )
