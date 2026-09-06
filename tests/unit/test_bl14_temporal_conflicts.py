"""BL-14 / ADR-007: temporal validity on graph relations.

Disjoint windows are distinct coexisting facts; overlapping windows are
adjudicated time-first, then by confidence. Relation ids embed the window.
"""

from __future__ import annotations

from agentic_graphrag.knowledge.graph_builder import triples_to_records
from agentic_graphrag.knowledge.incremental import IncrementalUpdater
from agentic_graphrag.knowledge.incremental_conflicts import windows_disjoint
from agentic_graphrag.knowledge.schema_check import EntityMention, Triple
from agentic_graphrag.stores.interfaces import RelationRecord
from agentic_graphrag.stores.memory_graph import InMemoryGraphStore

REL = "WORKED_AT"  # Person -> Company, matches configs/schema/domain_v0.yaml


def _triple(
    tail: str = "Acme",
    *,
    conf: float = 0.9,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> Triple:
    """Alice WORKED_AT <tail>, optionally valid over a period."""
    return Triple(
        head=EntityMention(name="Alice", type="Person"),
        relation=REL,
        tail=EntityMention(name=tail, type="Company"),
        confidence=conf,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _record(
    tail: str = "Acme",
    *,
    conf: float = 0.9,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> RelationRecord:
    _ents, rels = triples_to_records(
        [_triple(tail, conf=conf, valid_from=valid_from, valid_to=valid_to)]
    )
    return rels[0]


class TestWindowsDisjoint:
    def test_disjoint_periods(self) -> None:
        assert windows_disjoint(("2019", "2021"), ("2022", None)) is True

    def test_overlapping_periods(self) -> None:
        assert windows_disjoint(("2019", None), ("2020", "2023")) is False

    def test_open_ends_overlap(self) -> None:
        assert windows_disjoint((None, None), ("2020", None)) is False

    def test_missing_side_overlaps(self) -> None:
        # A side without time is unknown, never disjoint.
        assert windows_disjoint((None, None), ("2020", "2030")) is False

    def test_mixed_precision_iso_compares(self) -> None:
        assert windows_disjoint(("2024-03", None), ("2024", "2024-02")) is True


class TestRelationIdentity:
    def test_window_changes_relation_id(self) -> None:
        a = _record(valid_from="2019", valid_to="2021")
        b = _record(valid_from="2022", valid_to=None)
        assert a.id != b.id

    def test_same_window_same_id(self) -> None:
        a = _record(valid_from="2019", valid_to="2021")
        b = _record(valid_from="2019", valid_to="2021")
        assert a.id == b.id

    def test_no_window_keeps_fields_empty(self) -> None:
        plain = _record()
        assert plain.valid_from is None and plain.valid_to is None


class TestExactKeyTemporal:
    def test_disjoint_windows_are_clean_inserts(self) -> None:
        store = InMemoryGraphStore()
        store.upsert_relations([_record(valid_from="2019", valid_to="2021")])
        updater = IncrementalUpdater(store)
        result = updater.apply_batch([_triple(valid_from="2022")])
        # Not a conflict: both periods are true.
        assert result.conflicts_auto == 0
        assert result.conflicts_review == 0
        assert result.conflicts_kept == 0
        assert result.accepted == 1
        assert store.counts()["relationships"] == 2

    def test_newer_window_auto_updates(self) -> None:
        store = InMemoryGraphStore()
        # Both open-ended (valid_from, no end) → overlapping windows.
        store.upsert_relations([_record(tail="Acme Co", valid_from="2019")])
        updater = IncrementalUpdater(store)
        result = updater.apply_batch([_triple(tail="Acme Co", valid_from="2022")])
        assert result.conflicts_auto == 1
        assert result.review_items == []

    def test_older_window_keeps_existing(self) -> None:
        store = InMemoryGraphStore()
        store.upsert_relations([_record(tail="Acme Co", valid_from="2022")])
        updater = IncrementalUpdater(store)
        result = updater.apply_batch([_triple(tail="Acme Co", valid_from="2019")])
        assert result.conflicts_kept == 1
        assert result.accepted == 0

    def test_missing_window_falls_back_to_confidence(self) -> None:
        store = InMemoryGraphStore()
        store.upsert_relations([_record(tail="Acme Co", conf=0.9)])
        updater = IncrementalUpdater(store)
        # Lower confidence, no time edge: KEEP_OLD via the confidence policy.
        result = updater.apply_batch([_triple(tail="Acme Co", conf=0.6)])
        assert result.conflicts_kept == 1


class TestValueConflictTemporal:
    def test_disjoint_rival_is_not_a_conflict(self) -> None:
        store = InMemoryGraphStore()
        store.upsert_relations([_record(tail="Acme", valid_from="2019", valid_to="2021")])
        updater = IncrementalUpdater(store)
        result = updater.apply_batch([_triple(tail="Bob Industries", valid_from="2022")])
        assert result.conflicts_auto == 0
        assert result.conflicts_review == 0
        assert result.accepted == 1
        assert store.counts()["relationships"] == 2

    def test_overlapping_rival_newer_wins_by_time_not_confidence(self) -> None:
        store = InMemoryGraphStore()
        # Incumbent: Acme, started 2019, high confidence.
        store.upsert_relations([_record(tail="Acme", conf=0.95, valid_from="2019")])
        updater = IncrementalUpdater(store)
        # Incoming: Bob Industries, started 2022, lower confidence — time decides.
        result = updater.apply_batch([_triple(tail="Bob Industries", conf=0.6, valid_from="2022")])
        assert result.conflicts_auto == 1
        assert result.review_items == []
        assert result.accepted == 1

    def test_overlapping_rival_older_keeps_existing(self) -> None:
        store = InMemoryGraphStore()
        store.upsert_relations([_record(tail="Acme", conf=0.6, valid_from="2022")])
        updater = IncrementalUpdater(store)
        # Both open-ended → overlapping windows; the incumbent started later.
        result = updater.apply_batch([_triple(tail="Bob Industries", conf=0.9, valid_from="2019")])
        # Existing fact is newer: keep it regardless of incoming confidence.
        assert result.conflicts_kept == 1
        assert result.accepted == 0
