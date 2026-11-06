"""Opt-in shadow writes: durable intent, resumable seeding, sealed atomic publication."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agentic_graphrag.core.knowledge import KnowledgeBase, KnowledgeScope
from agentic_graphrag.core.knowledge_versions import KnowledgeSnapshot, WriteIntent
from agentic_graphrag.core.ports.knowledge import KnowledgeStore
from agentic_graphrag.knowledge.generation_changes import generation_changes
from agentic_graphrag.knowledge.generation_recovery import index_generations, restore_seeded
from agentic_graphrag.stores.factory import StoreBundle
from agentic_graphrag.stores.scoped_bundle import scope_bundle
from agentic_graphrag.stores.snapshot_bundle import capture_snapshot, restore_records
from agentic_graphrag.stores.snapshot_payload import SnapshotPayload, visible_payload


@dataclass(frozen=True)
class GenerationWrite:
    intent: WriteIntent
    bundle: StoreBundle

    @property
    def scope(self) -> KnowledgeScope:
        return self.intent.scope

    @property
    def operation_id(self) -> str:
        return self.intent.operation_id

    @property
    def source_generation(self) -> str:
        return self.intent.source_generation

    @property
    def target_generation(self) -> str:
        return self.intent.target_generation

    @property
    def expected_revision(self) -> int:
        return self.intent.expected_revision


class GenerationPipeline:
    def __init__(self, bundle: StoreBundle, metadata: KnowledgeStore) -> None:
        self.bundle, self.metadata = bundle, metadata

    def prepare(
        self, scope: KnowledgeScope, *, operation_id: str, doc_ids: tuple[str, ...] = ()
    ) -> GenerationWrite:
        with self.bundle.docs.lock():
            intent = self.metadata.get_write(scope, operation_id)
            if intent is None:
                intent = self._begin(scope, operation_id=operation_id, doc_ids=doc_ids)
            bound = scope_bundle(
                self.bundle,
                scope,
                generation=intent.target_generation,
                read_only=intent.status == "completed",
            )
            if intent.stage == "pending":
                source = visible_payload(
                    self._payload(scope, intent.source_generation), self.metadata
                )
                restore_records(bound, source)
                intent = self.metadata.mark_seeded(
                    scope, operation_id, index_generations=index_generations(bound)
                )
            elif intent.status == "pending":
                source = visible_payload(
                    self._payload(scope, intent.source_generation), self.metadata
                )
                restore_seeded(bound, source, previous=intent.index_generations)
                intent = self.metadata.mark_seeded(
                    scope, operation_id, index_generations=index_generations(bound)
                )
            return GenerationWrite(intent, bound)

    def _begin(
        self, scope: KnowledgeScope, *, operation_id: str, doc_ids: tuple[str, ...]
    ) -> WriteIntent:
        kb = self.metadata.get(scope)
        if kb is None:
            raise ValueError("Knowledge base does not exist")
        if self.metadata.get_snapshot(scope, kb.active_generation) is None:
            active = scope_bundle(self.bundle, scope, generation=kb.active_generation)
            capture_snapshot(active, _manifest(kb, kb.active_generation), store=self.metadata)
        target = "g-" + hashlib.sha256(operation_id.encode()).hexdigest()[:24]
        return self.metadata.begin_write(
            WriteIntent(
                operation_id=operation_id,
                scope=scope,
                source_generation=kb.active_generation,
                target_generation=target,
                document_ids=doc_ids,
                expected_revision=kb.config_revision,
            )
        )

    def complete(
        self, write: GenerationWrite, *, target_config: KnowledgeBase | None = None
    ) -> KnowledgeBase:
        with self.bundle.docs.lock():
            kb = self.metadata.get(write.scope)
            if kb is None:
                raise ValueError("Knowledge base does not exist")
            _validate_target(write, target_config)
            if self.metadata.get_snapshot(write.scope, write.target_generation) is None:
                self._check_ready(write)
                capture_snapshot(
                    write.bundle,
                    _manifest(target_config or kb, write.target_generation),
                    store=self.metadata,
                )
            before = self._payload(write.scope, write.source_generation)
            after = self._payload(write.scope, write.target_generation)
            change = generation_changes(write.intent, before, after)
            intent = self.metadata.get_write(write.scope, write.operation_id)
            if intent is None:
                raise ValueError("Write intent does not exist")
            if intent.status != "completed":
                self.metadata.set_write_documents(
                    write.scope,
                    write.operation_id,
                    document_ids=change.document_ids,
                )
            return self.metadata.commit_generation(
                change, expected_revision=write.expected_revision, target_config=target_config
            )

    def _payload(self, scope: KnowledgeScope, generation: str) -> SnapshotPayload:
        snapshot = self.metadata.get_snapshot(scope, generation)
        if snapshot is None:
            raise ValueError("Source generation has no sealed snapshot")
        return SnapshotPayload.model_validate_json(snapshot[1])

    def _check_ready(self, write: GenerationWrite) -> None:
        before = {
            doc.doc_id: doc for doc in self._payload(write.scope, write.source_generation).documents
        }
        for doc_id in write.bundle.docs.list_ids(tenant_id=write.scope.tenant_id):
            doc = write.bundle.docs.get(doc_id, tenant_id=write.scope.tenant_id)
            if doc is None:
                continue
            old = before.get(doc_id)
            changed = old is None or (old.version, old.content_sha256) != (
                doc.version,
                doc.content_sha256,
            )
            if changed and (
                doc.metadata.get("lifecycle") or not doc.metadata.get("index_complete")
            ):
                raise ValueError("Changed documents must finish indexing before publication")


def _manifest(kb: KnowledgeBase, generation: str) -> KnowledgeSnapshot:
    return KnowledgeSnapshot(
        scope=kb.scope,
        generation=generation,
        graph_version=generation,
        vector_version=generation,
        schema_version=kb.schema_version or "",
        model_config_revision=str(kb.config_revision),
        knowledge_base=kb,
    )


def _validate_target(write: GenerationWrite, target: KnowledgeBase | None) -> None:
    if target is None:
        return
    if target.scope != write.scope or target.config_revision != write.expected_revision:
        raise ValueError("Target configuration does not match the write scope and revision")
    if target.active_generation not in {write.source_generation, write.target_generation}:
        raise ValueError("Target configuration names an unrelated generation")
