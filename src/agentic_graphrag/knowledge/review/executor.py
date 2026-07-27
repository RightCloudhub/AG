"""Review decision executor — apply approved decisions to the graph (BL-03)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_graphrag.knowledge.graph_builder import (
    triples_to_records,
)
from agentic_graphrag.knowledge.review.queue import (
    ReviewDecision,
    ReviewQueue,
    ReviewStatus,
    ReviewType,
)
from agentic_graphrag.stores.interfaces import GraphStore

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    item_id: str
    success: bool
    action_taken: str
    detail: str = ""


@dataclass
class ApplyBatchResult:
    total_processed: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[ApplyResult] | None = None


class ReviewExecutor:
    """Apply approved review decisions to the knowledge graph."""

    def __init__(
        self,
        queue: ReviewQueue,
        store: GraphStore,
        *,
        doc_store: Any | None = None,
        journal_path: str | Path | None = None,
    ) -> None:
        self.queue = queue
        self.store = store
        self.doc_store = doc_store
        self.journal_path = Path(journal_path) if journal_path else None

    def apply_pending(self, *, limit: int = 50) -> ApplyBatchResult:
        """Process approved but unexecuted review items up to ``limit``."""
        approved = self.queue.list(
            status=ReviewStatus.APPROVED.value,
            limit=limit,
        )
        result = ApplyBatchResult(results=[])

        for item in approved:
            if _is_already_executed(item):
                self.queue.decide(
                    item.id,
                    ReviewDecision.SKIP,
                    note="Already executed",
                )
                continue

            apply_result = self._apply_item(item)
            result.results.append(apply_result)
            result.total_processed += 1
            result.succeeded += apply_result.success
            result.failed += not apply_result.success

        return result

    def _apply_item(self, item: Any) -> ApplyResult:
        """Dispatch to the correct executor by review type."""
        try:
            payload = item.payload
            if item.type == ReviewType.EXTRACTION.value:
                return self._apply_extraction(item, payload)
            if item.type == ReviewType.RESOLUTION.value:
                return self._apply_resolution(item, payload)
            logger.warning(
                "Unknown review type %s for item %s — skipping",
                item.type,
                item.id,
            )
            return ApplyResult(
                item_id=item.id,
                success=True,
                action_taken="skipped_unknown_type",
                detail=f"Type {item.type} has no executor",
            )
        except Exception as exc:  # noqa: BLE001 — log and continue
            logger.error("Failed to apply review item %s: %s", item.id, exc)
            return ApplyResult(
                item_id=item.id,
                success=False,
                action_taken="error",
                detail=type(exc).__name__,
            )

    def _apply_extraction(self, item: Any, payload: dict[str, Any]) -> ApplyResult:
        """Upsert approved triples into the graph store."""
        triples = payload.get("triples", [])
        if not triples:
            return ApplyResult(
                item_id=item.id,
                success=True,
                action_taken="no_triples",
                detail="Extraction review had no triples to apply",
            )

        accepted: list[Any] = []
        for t_raw in triples:
            try:
                from agentic_graphrag.knowledge.schema_check import Triple

                accepted.append(Triple(**t_raw))
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping malformed triple in item %s: %s", item.id, exc)

        if not accepted:
            return ApplyResult(
                item_id=item.id,
                success=True,
                action_taken="no_valid_triples",
                detail="All triples malformed after reconstruction",
            )

        entities, relations = triples_to_records(accepted)
        n_ent = self.store.upsert_entities(entities)
        n_rel = self.store.upsert_relations(relations)

        self._journal_execution(
            item.id,
            ReviewType.EXTRACTION.value,
            {
                "entities_upserted": n_ent,
                "relations_upserted": n_rel,
                "triples_applied": len(accepted),
            },
        )

        return ApplyResult(
            item_id=item.id,
            success=True,
            action_taken="triples_upserted",
            detail=f"{n_ent} entities, {n_rel} relations from {len(accepted)} triples",
        )

    def _apply_resolution(self, item: Any, payload: dict[str, Any]) -> ApplyResult:
        """Apply entity merge / resolution decision via resolution_merge."""
        keep_name = payload.get("keep_name") or payload.get("canonical_name")
        drop_name = payload.get("drop_name") or payload.get("merge_into")
        if not keep_name or not drop_name:
            logger.warning("Resolution review %s missing keep/drop names: %s", item.id, payload)
            return ApplyResult(
                item_id=item.id,
                success=False,
                action_taken="missing_names",
                detail=f"Need keep_name and drop_name; got {payload.keys()}",
            )
        from agentic_graphrag.knowledge.resolution_merge import (
            MergeApply,
            apply_entity_merge,
        )

        keep = self.store.get_entity_by_name(keep_name)
        drop = self.store.get_entity_by_name(drop_name)
        if not keep or not drop:
            missing = "keep" if not keep else "drop"
            return ApplyResult(
                item_id=item.id,
                success=False,
                action_taken=f"{missing}_entity_missing",
                detail=f"Cannot find {missing} entity",
            )

        canonical = payload.get("canonical", keep_name)
        apply_entity_merge(MergeApply(store=self.store, keep=keep, drop=drop, canonical=canonical))
        self._journal_execution(
            item.id,
            ReviewType.RESOLUTION.value,
            {"keep": keep_name, "drop": drop_name, "canonical": canonical},
        )
        return ApplyResult(
            item_id=item.id,
            success=True,
            action_taken="entity_merged",
            detail=f"Merged {drop_name} into {keep_name} (canonical: {canonical})",
        )

    def _journal_execution(self, item_id: str, item_type: str, detail: dict[str, Any]) -> None:
        """Append an execution record to the journal file."""
        if not self.journal_path:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        import json

        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "id": str(uuid.uuid4()),
                        "review_item_id": item_id,
                        "type": item_type,
                        "detail": detail,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _is_already_executed(item: Any) -> bool:
    """Check whether this approved item was already applied (by journal replay)."""
    meta = item.payload.get("execution_meta") or {}
    return bool(meta.get("executed_at"))
