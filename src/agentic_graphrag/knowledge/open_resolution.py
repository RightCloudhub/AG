"""Alias/vector candidates require contextual judgment before canonical identity changes."""

from __future__ import annotations

import json
from dataclasses import dataclass

from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import MessageRole, PromptName
from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.capabilities import EntityLookup
from agentic_graphrag.core.ports.entities import EntityEmbedder, EntitySemanticIndex, SemanticEntity
from agentic_graphrag.core.ports.llm import CompletionClient, Message
from agentic_graphrag.core.ports.stores import EntityRecord, GraphStore
from agentic_graphrag.knowledge.open_contracts import OpenEntity, ResolutionVerdict
from agentic_graphrag.knowledge.open_sources import canonical_id, mention_id
from agentic_graphrag.llm.structured import complete_structured

ENTITY_CANDIDATE_LIMIT = 12


@dataclass
class ResolvedEntity:
    local_ref: str
    mention_id: str
    source: SourceRef
    record: EntityRecord
    semantic: SemanticEntity
    review_candidates: tuple[str, ...] = ()


@dataclass
class OpenResolver:
    graph: GraphStore
    index: EntitySemanticIndex
    embedder: EntityEmbedder
    space: str
    judge: CompletionClient | None = None

    def resolve(
        self, mention: OpenEntity, source: SourceRef, *, vector: list[float]
    ) -> ResolvedEntity:
        mid = mention_id(source, mention.name)
        existing = self._known(mid, mention.name, source.scope)
        candidates = [] if existing else self._candidates(mention, vector, source.scope)
        selected, review = (existing, ()) if existing else self._judge(mention, candidates)
        identity = selected.entity_id if selected else canonical_id(mid)
        description = _combined(selected.description if selected else "", mention.description)
        if selected and description != mention.description:
            vectors = self.embedder.embed_many([description])
            if len(vectors) != 1:
                raise ValueError("Entity embedding returned the wrong number of vectors")
            vector = vectors[0]
        semantic = SemanticEntity(
            scope=source.scope,
            entity_id=identity,
            name=selected.name if selected else mention.name,
            entity_type=mention.entity_type,
            description=description,
            vector=tuple(vector),
            aliases=tuple(
                dict.fromkeys(
                    [*(selected.aliases if selected else ()), *mention.aliases, mention.name]
                )
            ),
            sources=tuple(dict.fromkeys([*(selected.sources if selected else ()), source])),
        )
        record = EntityRecord(
            id=identity,
            name=semantic.name,
            type=semantic.entity_type,
            tenant_id=source.scope.tenant_id,
            kb_id=source.scope.kb_id,
            attributes={
                "graph_mode": "open",
                "description": description,
                "embedding_space": self.space,
            },
            aliases=list(semantic.aliases),
            sources=[s.model_dump(mode="json") for s in semantic.sources],
        )
        return ResolvedEntity(mention.ref, mid, source, record, semantic, review)

    def _known(self, mid: str, name: str, scope: KnowledgeScope) -> SemanticEntity | None:
        identity = self.index.mention(mid, scope=scope, space=self.space)
        if identity is not None and self._visible(identity, scope):
            return self.index.get(identity, scope=scope, space=self.space)
        identity = self.index.confirmed_entity(name, scope=scope, space=self.space)
        if identity and self._visible(identity, scope):
            return self.index.get(identity, scope=scope, space=self.space)
        return None

    def _visible(self, identity: str, scope: KnowledgeScope) -> bool:
        if not isinstance(self.graph, EntityLookup):
            raise TypeError("Open resolution requires graph lookup by canonical ID")
        return self.graph.get_entity_by_id(identity, tenant_id=scope.tenant_id) is not None

    def _candidates(
        self, mention: OpenEntity, vector: list[float], scope: KnowledgeScope
    ) -> list[SemanticEntity]:
        aliases = self.index.aliases(
            mention.name, scope=scope, space=self.space, top_k=ENTITY_CANDIDATE_LIMIT
        )
        similar = self.index.search(
            vector, scope=scope, space=self.space, top_k=ENTITY_CANDIDATE_LIMIT
        )
        candidates = {item.entity_id: item for item in [*aliases, *(row.entity for row in similar)]}
        return [item for item in candidates.values() if self._visible(item.entity_id, scope)][
            :ENTITY_CANDIDATE_LIMIT
        ]

    def _judge(
        self, mention: OpenEntity, candidates: list[SemanticEntity]
    ) -> tuple[SemanticEntity | None, tuple[str, ...]]:
        if not candidates:
            return None, ()
        ids = tuple(candidate.entity_id for candidate in candidates)
        if self.judge is None:
            return None, ids
        messages = [
            Message(role=MessageRole.SYSTEM, content=load_prompt(PromptName.OPEN_RESOLUTION)),
            Message(
                role=MessageRole.USER,
                content=json.dumps(
                    {
                        "mention": mention.model_dump(mode="json"),
                        "candidates": [
                            c.model_dump(mode="json", exclude={"vector"}) for c in candidates
                        ],
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        verdict = complete_structured(self.judge, messages, ResolutionVerdict, max_retries=1)
        if verdict.action == "merge":
            chosen = next((c for c in candidates if c.entity_id == verdict.candidate_id), None)
            return (chosen, ()) if chosen else (None, ids)
        return None, ids if verdict.action == "review" else ()


def _combined(old: str, new: str) -> str:
    return "\n".join(dict.fromkeys(filter(None, [old, new])))
