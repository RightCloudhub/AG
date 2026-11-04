"""Copy confirmed identity only for retained entities and original source versions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.entities import SemanticEntity

if TYPE_CHECKING:
    from agentic_graphrag.stores.entity_index import SQLiteEntityIndex


def copy_identity(
    index: SQLiteEntityIndex,
    entities: list[SemanticEntity],
    *,
    scope: KnowledgeScope,
    source_space: str,
    target_space: str,
) -> None:
    from agentic_graphrag.stores.entity_index import _scope

    if any(entity.scope != scope for entity in entities):
        raise ValueError("Identity transfer belongs to another knowledge scope")
    retained = {entity.entity_id for entity in entities if entity.sources}
    sources = {ref for entity in entities for ref in entity.sources}
    owner = _scope(scope)
    spaces = (source_space, target_space)
    with index._lock, index._db:
        _aliases(index, owner, entities, scope=scope, spaces=spaces)
        _mentions(index, owner, scope=scope, spaces=spaces, retained=retained, sources=sources)
        _redirects(
            index,
            owner,
            scope=scope,
            source_space=source_space,
            target_space=target_space,
            retained=retained,
        )


def _aliases(
    index: SQLiteEntityIndex,
    owner: str,
    entities: list[SemanticEntity],
    *,
    scope: KnowledgeScope,
    spaces: tuple[str, str],
) -> None:
    retained = {
        entity.entity_id
        for entity in entities
        if entity.sources
        and (old := index.get(entity.entity_id, scope=scope, space=spaces[0])) is not None
        and set(old.sources) == set(entity.sources)
    }
    rows = index._db.execute(
        "SELECT alias,id FROM aliases WHERE scope=? AND space=? AND confirmed=1",
        (owner, spaces[0]),
    ).fetchall()
    for alias, identity in rows:
        target = index._resolve(identity, scope=scope, space=spaces[0])
        if target in retained:
            index._db.execute(
                "INSERT OR IGNORE INTO aliases VALUES (?,?,?,?,1)",
                (owner, spaces[1], alias, target),
            )


def _mentions(
    index: SQLiteEntityIndex,
    owner: str,
    *,
    scope: KnowledgeScope,
    spaces: tuple[str, str],
    retained: set[str],
    sources: set[SourceRef],
) -> None:
    rows = index._db.execute(
        "SELECT mention,id,source FROM mentions WHERE scope=? AND space=?", (owner, spaces[0])
    ).fetchall()
    for mention, identity, serialized in rows:
        target = index._resolve(identity, scope=scope, space=spaces[0])
        if target in retained and SourceRef.model_validate_json(serialized) in sources:
            index._db.execute(
                "INSERT OR IGNORE INTO mentions VALUES (?,?,?,?,?)",
                (owner, spaces[1], mention, target, serialized),
            )


def _redirects(
    index: SQLiteEntityIndex,
    owner: str,
    *,
    scope: KnowledgeScope,
    source_space: str,
    target_space: str,
    retained: set[str],
) -> None:
    rows = index._db.execute(
        "SELECT old_id,new_id,operation_id FROM entity_redirects WHERE scope=? AND space=?",
        (owner, source_space),
    ).fetchall()
    for old_id, new_id, operation in rows:
        target = index._resolve(new_id, scope=scope, space=source_space)
        if target in retained:
            index._db.execute(
                "INSERT OR IGNORE INTO entity_redirects VALUES (?,?,?,?,?)",
                (owner, target_space, old_id, target, operation),
            )
