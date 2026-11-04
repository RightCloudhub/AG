"""Immutable persisted projections and partitions for generation-to-generation repair."""

import hashlib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from agentic_graphrag.core.communities import Community, GraphProjection
from agentic_graphrag.core.knowledge import KnowledgeScope
from agentic_graphrag.stores.knowledge_database import KnowledgeDatabase, scope_params


class _Partition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    projection: GraphProjection
    communities: tuple[Community, ...]

    @model_validator(mode="after")
    def exact_coverage(self) -> Self:
        if any(
            (item.scope, item.generation) != (self.projection.scope, self.projection.generation)
            for item in self.communities
        ):
            raise ValueError("Community partition scope or generation differs from its projection")
        members = [entity for item in self.communities for entity in item.entity_ids]
        if len(members) != len(set(members)) or set(members) != {
            node.id for node in self.projection.nodes
        }:
            raise ValueError("Every projected entity must belong to exactly one community")
        return self


class CommunityIndexStore(KnowledgeDatabase):
    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS community_partitions "
                "(tenant TEXT NOT NULL,kb TEXT NOT NULL,generation TEXT NOT NULL,"
                "payload TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(tenant,kb,generation))"
            )

    def get(
        self, scope: KnowledgeScope, generation: str
    ) -> tuple[GraphProjection, tuple[Community, ...]] | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload,digest FROM community_partitions "
                "WHERE tenant=? AND kb=? AND generation=?",
                (*scope_params(scope), generation),
            ).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("Stored community partition integrity check failed")
        partition = _Partition.model_validate_json(row[0])
        if (partition.projection.scope, partition.projection.generation) != (scope, generation):
            raise ValueError("Stored community partition belongs to another scope")
        return partition.projection, partition.communities

    def save(self, projection: GraphProjection, communities: tuple[Community, ...]) -> None:
        partition = _Partition(projection=projection, communities=communities)
        payload = partition.model_dump_json()
        key = (*scope_params(projection.scope), projection.generation)
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM community_partitions WHERE tenant=? AND kb=? AND generation=?",
                key,
            ).fetchone()
            if row:
                if _Partition.model_validate_json(row[0]) != partition:
                    raise ValueError("A sealed community partition cannot be overwritten")
                return
            db.execute(
                "INSERT INTO community_partitions VALUES (?,?,?,?,?)",
                (*key, payload, hashlib.sha256(payload.encode()).hexdigest()),
            )
