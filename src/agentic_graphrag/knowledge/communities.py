"""Optional local Leiden adapter and an explicit deterministic fixture detector."""

import importlib
import math

from agentic_graphrag.core.communities import Community, GraphProjection
from agentic_graphrag.knowledge.community_records import community_for_members, component_members


class LeidenDetector:
    algorithm = "leiden-rb-configuration"

    def __init__(self, *, resolution: float = 1.0) -> None:
        if not math.isfinite(resolution) or resolution <= 0:
            raise ValueError("Leiden resolution must be positive and finite")
        self.resolution = resolution

    def detect(self, projection: GraphProjection, *, seed: int = 0) -> tuple[Community, ...]:
        if not projection.nodes:
            return ()
        if seed < 0:
            raise ValueError("Leiden seed must be nonnegative")
        try:
            igraph = importlib.import_module("igraph")
            leidenalg = importlib.import_module("leidenalg")
        except ModuleNotFoundError as exc:
            raise RuntimeError("Leiden requires the optional summary dependency group") from exc
        indices = {node.id: index for index, node in enumerate(projection.nodes)}
        graph = igraph.Graph(
            n=len(indices),
            edges=[(indices[e.source], indices[e.target]) for e in projection.edges],
            directed=False,
        )
        partition = leidenalg.find_partition(
            graph,
            leidenalg.RBConfigurationVertexPartition,
            weights=[edge.weight for edge in projection.edges],
            seed=seed,
            n_iterations=-1,
            resolution_parameter=self.resolution,
        )
        groups = [{projection.nodes[int(index)].id for index in members} for members in partition]
        communities = [community_for_members(projection, members) for members in groups]
        return tuple(sorted(communities, key=lambda item: item.id))


class ConnectedComponentsDetector:
    """Fixture detector; deliberately not a silent replacement for Leiden."""

    algorithm = "connected-components-fixture"

    def detect(self, projection: GraphProjection, *, seed: int = 0) -> tuple[Community, ...]:
        return tuple(
            sorted(
                (
                    community_for_members(projection, set(members))
                    for members in component_members(projection)
                ),
                key=lambda item: item.id,
            )
        )
