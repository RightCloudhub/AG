"""Immutable generation manifests, payloads and irrevocable source tombstones."""

import hashlib
import json

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope, SummaryRecord
from agentic_graphrag.core.knowledge_versions import GraphChangeSet, KnowledgeSnapshot
from agentic_graphrag.stores.knowledge_database import read_kb, scope_params, write_kb
from agentic_graphrag.stores.knowledge_journal import KnowledgeJournal, complete_record
from agentic_graphrag.stores.knowledge_summaries import save_summary_record


class KnowledgeSnapshots(KnowledgeJournal):
    def save_snapshot(self, snapshot: KnowledgeSnapshot, payload: str) -> KnowledgeSnapshot:
        canonical = json.dumps(json.loads(payload), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        if snapshot.payload_sha256 and snapshot.payload_sha256 != digest:
            raise ValueError("Snapshot payload does not match its manifest hash")
        result = snapshot.model_copy(update={"payload_sha256": digest})
        key = (*scope_params(snapshot.scope), snapshot.generation)
        with self.transaction() as db:
            row = db.execute(
                "SELECT manifest,payload FROM knowledge_snapshots "
                "WHERE tenant=? AND kb=? AND generation=?",
                key,
            ).fetchone()
            if row:
                stored = KnowledgeSnapshot.model_validate_json(row[0])
                if stored.model_dump(exclude={"created_at"}) != result.model_dump(
                    exclude={"created_at"}
                ):
                    raise ValueError("A sealed generation cannot be overwritten")
                return stored
            db.execute(
                "INSERT INTO knowledge_snapshots VALUES (?,?,?,?,?)",
                (*key, result.model_dump_json(), canonical),
            )
        return result

    def get_snapshot(
        self, scope: KnowledgeScope, generation: str
    ) -> tuple[KnowledgeSnapshot, str] | None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT manifest,payload FROM knowledge_snapshots "
                "WHERE tenant=? AND kb=? AND generation=?",
                (*scope_params(scope), generation),
            ).fetchone()
        if not row:
            return None
        manifest = KnowledgeSnapshot.model_validate_json(row[0])
        if hashlib.sha256(row[1].encode()).hexdigest() != manifest.payload_sha256:
            raise ValueError("Snapshot payload integrity check failed")
        return manifest, row[1]

    def publish(
        self, scope: KnowledgeScope, *, generation: str, expected_revision: int
    ) -> KnowledgeBase:
        with self.transaction() as db:
            kb = read_kb(db, scope)
            exists = db.execute(
                "SELECT manifest FROM knowledge_snapshots WHERE tenant=? AND kb=? AND generation=?",
                (*scope_params(scope), generation),
            ).fetchone()
            if kb is None or not exists:
                raise ValueError("Only a sealed knowledge base generation can be published")
            snapshot = KnowledgeSnapshot.model_validate_json(exists[0])
            restored = snapshot.knowledge_base or kb
            if restored.scope != scope:
                raise ValueError("Stored generation configuration has another scope")
            updated = KnowledgeBase.model_validate(
                {
                    **restored.model_dump(),
                    "active_generation": generation,
                    "config_revision": expected_revision + 1,
                }
            )
            write_kb(db, updated, expected_revision=expected_revision)
        return updated

    def commit_generation(
        self,
        change: GraphChangeSet,
        *,
        expected_revision: int,
        target_config: KnowledgeBase | None = None,
    ) -> KnowledgeBase:
        """The change journal and active pointer commit in one metadata transaction."""
        scope = change.scope
        with self.transaction() as db:
            kb = read_kb(db, scope)
            snapshot = db.execute(
                "SELECT manifest FROM knowledge_snapshots WHERE tenant=? AND kb=? AND generation=?",
                (*scope_params(scope), change.target_generation),
            ).fetchone()
            if kb is None or not snapshot:
                raise ValueError("Only a sealed knowledge base generation can be published")
            manifest = KnowledgeSnapshot.model_validate_json(snapshot[0])
            proposed = _publication_config(kb, manifest, target=target_config)
            if kb.active_generation == change.target_generation:
                excluded = {"active_generation", "config_revision"}
                if kb.model_dump(exclude=excluded) != proposed.model_dump(exclude=excluded):
                    raise ValueError("Published generation configuration is immutable")
                complete_record(db, change)
                return kb
            if kb.active_generation != change.source_generation:
                raise ValueError("A newer generation has already been published")
            updated = KnowledgeBase.model_validate(
                {
                    **proposed.model_dump(),
                    "active_generation": change.target_generation,
                    "config_revision": expected_revision + 1,
                }
            )
            write_kb(db, updated, expected_revision=expected_revision)
            complete_record(db, change)
        return updated

    def tombstone(self, scope: KnowledgeScope, doc_id: str, *, through_version: int) -> None:
        if through_version < 1 or not doc_id:
            raise ValueError("A tombstone needs a document and a positive version")
        with self.transaction() as db:
            db.execute(
                "INSERT INTO source_tombstones VALUES (?,?,?,?) "
                "ON CONFLICT(tenant,kb,doc_id) DO UPDATE SET through_version="
                "max(through_version,excluded.through_version)",
                (*scope_params(scope), doc_id, through_version),
            )

    def is_tombstoned(self, scope: KnowledgeScope, doc_id: str, *, version: int) -> bool:
        with self.transaction() as db:
            row = db.execute(
                "SELECT through_version FROM source_tombstones "
                "WHERE tenant=? AND kb=? AND doc_id=?",
                (*scope_params(scope), doc_id),
            ).fetchone()
        return bool(row and version <= int(row[0]))

    def save_summary(self, summary: SummaryRecord) -> None:
        with self.transaction() as db:
            save_summary_record(db, summary)

    def summaries(self, scope: KnowledgeScope, *, generation: str) -> list[SummaryRecord]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT payload FROM knowledge_summaries WHERE tenant=? AND kb=? AND generation=? "
                "ORDER BY id",
                (*scope_params(scope), generation),
            ).fetchall()
        return [SummaryRecord.model_validate_json(row[0]) for row in rows]


def _publication_config(
    current: KnowledgeBase,
    manifest: KnowledgeSnapshot,
    *,
    target: KnowledgeBase | None,
) -> KnowledgeBase:
    proposed = target or manifest.knowledge_base or current
    if proposed.scope != current.scope:
        raise ValueError("Generation configuration has another knowledge scope")
    if target is not None and target != manifest.knowledge_base:
        raise ValueError("Target configuration differs from the sealed generation")
    return proposed
