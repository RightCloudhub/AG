"""Reproducible graph projections, communities and incremental repair reports."""

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.core.knowledge import KnowledgeScope


class ProjectedNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    document_ids: tuple[str, ...] = ()
    content_fingerprint: str


class ProjectedEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: str
    target: str
    weight: float = Field(gt=0, allow_inf_nan=False)
    document_ids: tuple[str, ...] = ()
    relation_ids: tuple[str, ...] = ()
    content_fingerprint: str


class GraphProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: KnowledgeScope
    generation: str
    nodes: tuple[ProjectedNode, ...]
    edges: tuple[ProjectedEdge, ...]


class Community(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    scope: KnowledgeScope
    generation: str
    entity_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    topology_fingerprint: str
    content_fingerprint: str
    input_fingerprint: str


class CommunityUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    communities: tuple[Community, ...]
    recomputed_entity_ids: tuple[str, ...] = ()
    reused_community_ids: tuple[str, ...] = ()
    changed_community_ids: tuple[str, ...] = ()
    removed_community_ids: tuple[str, ...] = ()
    topology_changed: bool = False
    full_rebuild: bool = False
    # Component repair can differ from a full global modularity optimization.
    exact_full_partition: bool = False
