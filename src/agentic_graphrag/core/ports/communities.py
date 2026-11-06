"""Community detection is a backend capability, independent of graph storage."""

from typing import Protocol

from agentic_graphrag.core.communities import Community, GraphProjection
from agentic_graphrag.core.knowledge import KnowledgeScope


class CommunityDetector(Protocol):
    def detect(self, projection: GraphProjection, *, seed: int = 0) -> tuple[Community, ...]: ...


class CommunityIndex(Protocol):
    def get(
        self, scope: KnowledgeScope, generation: str
    ) -> tuple[GraphProjection, tuple[Community, ...]] | None: ...

    def save(self, projection: GraphProjection, communities: tuple[Community, ...]) -> None: ...
