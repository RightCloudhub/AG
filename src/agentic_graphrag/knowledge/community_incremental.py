"""Component-bounded community repair with explicit reuse and equivalence limits."""

from agentic_graphrag.core.communities import Community, CommunityUpdate, GraphProjection
from agentic_graphrag.core.knowledge_versions import GraphChangeSet
from agentic_graphrag.core.ports.communities import CommunityDetector
from agentic_graphrag.knowledge.community_records import (
    community_for_members,
    component_members,
    subprojection,
)


def update_communities(
    before: GraphProjection,
    after: GraphProjection,
    *,
    previous: tuple[Community, ...],
    change: GraphChangeSet,
    detector: CommunityDetector,
    seed: int = 0,
    full_rebuild_ratio: float = 0.75,
) -> CommunityUpdate:
    """Graph diffs, rather than a change-event hint alone, determine invalidation.

    Repairs include complete old and new affected components. A local modularity
    solution can differ from optimizing the whole graph; the result states this.
    """
    _validate_update(before, after, previous=previous, change=change)
    if not 0 < full_rebuild_ratio <= 1:
        raise ValueError("Full rebuild ratio must be in (0,1]")
    changed = topology_changes(before, after)
    members = expanded_components(before, after, changed)
    current_ids = {node.id for node in after.nodes}
    full = bool(changed) and len(members & current_ids) >= full_rebuild_ratio * len(current_ids)
    if full:
        communities = detector.detect(after, seed=seed)
        recomputed = current_ids
    else:
        recomputed = members & current_ids
        communities = _repair(
            after, previous=previous, affected=members, detector=detector, seed=seed
        )
    return _report(previous, communities, recomputed=recomputed, topology=bool(changed), full=full)


def _validate_update(
    before: GraphProjection,
    after: GraphProjection,
    *,
    previous: tuple[Community, ...],
    change: GraphChangeSet,
) -> None:
    if before.scope != after.scope or change.scope != after.scope:
        raise ValueError("Community update crosses knowledge scopes")
    _validate_partition(before, previous)
    if (before.generation, after.generation) != (
        change.source_generation,
        change.target_generation,
    ):
        raise ValueError("Community update does not match graph change generations")


def _validate_partition(before: GraphProjection, previous: tuple[Community, ...]) -> None:
    if any(item.scope != before.scope or item.generation != before.generation for item in previous):
        raise ValueError("Previous partition has another scope or generation")
    members = [entity_id for community in previous for entity_id in community.entity_ids]
    if set(members) != {node.id for node in before.nodes} or len(members) != len(set(members)):
        raise ValueError("Previous community partition must cover every old entity exactly once")


def topology_changes(before: GraphProjection, after: GraphProjection) -> set[str]:
    changed = {node.id for node in before.nodes} ^ {node.id for node in after.nodes}
    old_edges = {(edge.source, edge.target): edge.weight for edge in before.edges}
    new_edges = {(edge.source, edge.target): edge.weight for edge in after.edges}
    for endpoints in old_edges.keys() | new_edges.keys():
        if old_edges.get(endpoints) != new_edges.get(endpoints):
            changed.update(endpoints)
    return changed


def expanded_components(
    before: GraphProjection, after: GraphProjection, changed: set[str]
) -> set[str]:
    affected = set(changed)
    components = (*component_members(before), *component_members(after))
    while True:
        expanded = affected | {
            member for group in components if group & affected for member in group
        }
        if expanded == affected:
            return affected
        affected = expanded


def _repair(
    after: GraphProjection,
    *,
    previous: tuple[Community, ...],
    affected: set[str],
    detector: CommunityDetector,
    seed: int,
) -> tuple[Community, ...]:
    retained = [
        community_for_members(after, set(community.entity_ids))
        for community in previous
        if not set(community.entity_ids) & affected
    ]
    if affected:
        retained.extend(detector.detect(subprojection(after, affected), seed=seed))
    return tuple(sorted(retained, key=lambda community: community.id))


def _report(
    before: tuple[Community, ...],
    after: tuple[Community, ...],
    *,
    recomputed: set[str],
    topology: bool,
    full: bool,
) -> CommunityUpdate:
    old = {community.id: community for community in before}
    new = {community.id: community for community in after}
    reused = tuple(
        sorted(
            identifier
            for identifier, value in new.items()
            if identifier in old and old[identifier].input_fingerprint == value.input_fingerprint
        )
    )
    return CommunityUpdate(
        communities=after,
        recomputed_entity_ids=tuple(sorted(recomputed)),
        reused_community_ids=reused,
        changed_community_ids=tuple(sorted(new.keys() - set(reused))),
        removed_community_ids=tuple(sorted(old.keys() - new.keys())),
        topology_changed=topology,
        full_rebuild=full,
        exact_full_partition=full,
    )
