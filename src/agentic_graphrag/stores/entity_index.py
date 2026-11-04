"""SQLite entity/alias index with exact vector top-k and durable mention identity.

The exact search adapter is suitable for the initial single-host deployment.
The port permits an ANN adapter without representing entities as document chunks.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path

from agentic_graphrag.core.knowledge import KnowledgeScope, SourceRef
from agentic_graphrag.core.ports.entities import EntityMatch, SemanticEntity


def _scope(scope: KnowledgeScope) -> str:
    return json.dumps([scope.tenant_id, scope.kb_id], ensure_ascii=True)


def _alias(value: str) -> str:
    return " ".join(value.casefold().split())


class SQLiteEntityIndex:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self._lock = threading.RLock()
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS entities (scope TEXT, space TEXT, id TEXT, data TEXT, "
            "PRIMARY KEY(scope,space,id));"
            "CREATE TABLE IF NOT EXISTS aliases (scope TEXT, space TEXT, alias TEXT, id TEXT, "
            "confirmed INTEGER DEFAULT 0, PRIMARY KEY(scope,space,alias,id));"
            "CREATE TABLE IF NOT EXISTS mentions (scope TEXT, space TEXT, mention TEXT, id TEXT, "
            "source TEXT, PRIMARY KEY(scope,space,mention));"
            "CREATE TABLE IF NOT EXISTS entity_spaces (scope TEXT, space TEXT, dimensions INTEGER, "
            "PRIMARY KEY(scope,space));"
            "CREATE TABLE IF NOT EXISTS entity_redirects (scope TEXT, space TEXT, old_id TEXT, "
            "new_id TEXT, operation_id TEXT, PRIMARY KEY(scope,space,old_id));"
        )

    def close(self) -> None:
        self._db.close()

    def copy_identity(
        self,
        entities: list[SemanticEntity],
        *,
        scope: KnowledgeScope,
        source_space: str,
        target_space: str,
    ) -> None:
        from agentic_graphrag.stores.entity_identity import copy_identity

        copy_identity(
            self, entities, scope=scope, source_space=source_space, target_space=target_space
        )

    def upsert(self, entities: list[SemanticEntity], *, space: str) -> None:
        if not space.strip():
            raise ValueError("A versioned entity embedding space is required")
        with self._lock, self._db:
            for entity in entities:
                owner = _scope(entity.scope)
                self._check_dimensions(owner, space, len(entity.vector))
                self._db.execute(
                    "INSERT OR REPLACE INTO entities VALUES (?,?,?,?)",
                    (owner, space, entity.entity_id, entity.model_dump_json()),
                )
                self._db.execute(
                    "DELETE FROM aliases WHERE scope=? AND space=? AND id=? AND confirmed=0",
                    (owner, space, entity.entity_id),
                )
                self._db.executemany(
                    "INSERT OR IGNORE INTO aliases VALUES (?,?,?,?,0)",
                    [
                        (owner, space, _alias(a), entity.entity_id)
                        for a in (entity.name, *entity.aliases)
                    ],
                )

    def _check_dimensions(self, owner: str, space: str, dimensions: int) -> None:
        current = self._db.execute(
            "SELECT dimensions FROM entity_spaces WHERE scope=? AND space=?", (owner, space)
        ).fetchone()
        if current and current[0] != dimensions:
            raise ValueError("Embedding dimensions changed within an entity space")
        self._db.execute(
            "INSERT OR IGNORE INTO entity_spaces VALUES (?,?,?)", (owner, space, dimensions)
        )

    def get(self, entity_id: str, *, scope: KnowledgeScope, space: str) -> SemanticEntity | None:
        with self._lock:
            entity_id = self._resolve(entity_id, scope=scope, space=space)
            row = self._db.execute(
                "SELECT data FROM entities WHERE scope=? AND space=? AND id=?",
                (_scope(scope), space, entity_id),
            ).fetchone()
        return SemanticEntity.model_validate_json(row[0]) if row else None

    def search(
        self, vector: list[float], *, scope: KnowledgeScope, space: str, top_k: int = 12
    ) -> list[EntityMatch]:
        if not vector or not all(math.isfinite(v) for v in vector):
            raise ValueError("Query embedding must contain finite values")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        with self._lock:
            rows = self._db.execute(
                "SELECT e.data FROM entities e LEFT JOIN entity_redirects r "
                "ON e.scope=r.scope AND e.space=r.space AND e.id=r.old_id "
                "WHERE e.scope=? AND e.space=? AND r.old_id IS NULL",
                (_scope(scope), space),
            ).fetchall()
        entities = [SemanticEntity.model_validate_json(row[0]) for row in rows]
        scores = [EntityMatch(entity=e, score=_cosine(vector, e.vector)) for e in entities]
        return sorted(scores, key=lambda item: (-item.score, item.entity.entity_id))[:top_k]

    def aliases(
        self, alias: str, *, scope: KnowledgeScope, space: str, top_k: int = 12
    ) -> list[SemanticEntity]:
        with self._lock:
            rows = self._db.execute(
                "SELECT e.data FROM entities e JOIN aliases a "
                "ON e.scope=a.scope AND e.space=a.space AND e.id=a.id "
                "WHERE a.scope=? AND a.space=? AND a.alias=? ORDER BY e.id LIMIT ?",
                (_scope(scope), space, _alias(alias), max(0, top_k)),
            ).fetchall()
        aliases = [SemanticEntity.model_validate_json(row[0]) for row in rows]
        resolved = [
            value
            for item in aliases
            if (value := self.get(item.entity_id, scope=scope, space=space)) is not None
        ]
        return list({item.entity_id: item for item in resolved}.values())

    def confirm_alias(
        self, alias: str, entity_id: str, *, scope: KnowledgeScope, space: str
    ) -> None:
        if self.get(entity_id, scope=scope, space=space) is None:
            raise KeyError("Unknown entity in this knowledge scope")
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO aliases VALUES (?,?,?,?,1)",
                (_scope(scope), space, _alias(alias), entity_id),
            )

    def confirmed_entity(self, alias: str, *, scope: KnowledgeScope, space: str) -> str | None:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM aliases WHERE scope=? AND space=? AND alias=? AND confirmed=1",
                (_scope(scope), space, _alias(alias)),
            ).fetchall()
            ids = {self._resolve(str(row[0]), scope=scope, space=space) for row in rows}
        return next(iter(ids)) if len(ids) == 1 else None

    def mention(self, mention_id: str, *, scope: KnowledgeScope, space: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM mentions WHERE scope=? AND space=? AND mention=?",
                (_scope(scope), space, mention_id),
            ).fetchone()
            return self._resolve(str(row[0]), scope=scope, space=space) if row else None

    def bind_mention(
        self, mention_id: str, entity_id: str, *, source: SourceRef, space: str
    ) -> None:
        if self.get(entity_id, scope=source.scope, space=space) is None:
            raise KeyError("Unknown canonical entity")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT id FROM mentions WHERE scope=? AND space=? AND mention=?",
                (_scope(source.scope), space, mention_id),
            ).fetchone()
            if row and self._resolve(str(row[0]), scope=source.scope, space=space) != entity_id:
                raise ValueError(
                    "An existing mention mapping requires an explicit review to change"
                )
            self._db.execute(
                "INSERT OR REPLACE INTO mentions VALUES (?,?,?,?,?)",
                (_scope(source.scope), space, mention_id, entity_id, source.model_dump_json()),
            )

    def _resolve(self, identity: str, *, scope: KnowledgeScope, space: str) -> str:
        seen: set[str] = set()
        while identity not in seen:
            seen.add(identity)
            row = self._db.execute(
                "SELECT new_id FROM entity_redirects WHERE scope=? AND space=? AND old_id=?",
                (_scope(scope), space, identity),
            ).fetchone()
            if row is None:
                return identity
            identity = str(row[0])
        raise ValueError("Entity redirects contain a cycle")

    def redirect(
        self, old_id: str, new_id: str, *, scope: KnowledgeScope, space: str, operation_id: str
    ) -> None:
        with self._lock, self._db:
            target = self._resolve(new_id, scope=scope, space=space)
            if target == old_id:
                raise ValueError("Entity redirect would create a cycle")
            if (
                self.get(old_id, scope=scope, space=space) is None
                or self.get(target, scope=scope, space=space) is None
            ):
                raise KeyError("Cannot redirect an unknown entity")
            row = self._db.execute(
                "SELECT new_id,operation_id FROM entity_redirects "
                "WHERE scope=? AND space=? AND old_id=?",
                (_scope(scope), space, old_id),
            ).fetchone()
            if row and tuple(row) != (target, operation_id):
                raise ValueError("Entity already has another merge decision")
            self._db.execute(
                "INSERT OR IGNORE INTO entity_redirects VALUES (?,?,?,?,?)",
                (_scope(scope), space, old_id, target, operation_id),
            )

    def remove_redirect(
        self, old_id: str, *, scope: KnowledgeScope, space: str, operation_id: str
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM entity_redirects WHERE scope=? AND space=? "
                "AND old_id=? AND operation_id=?",
                (_scope(scope), space, old_id, operation_id),
            )


def _cosine(left: list[float], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("Entity embedding dimensions do not match the query")
    norm = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
    score = sum(a * b for a, b in zip(left, right, strict=True)) / norm if norm else 0.0
    return max(-1.0, min(1.0, score))
