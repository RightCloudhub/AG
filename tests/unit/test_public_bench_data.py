"""B-01/B-02 contracts: manifest validation + hash, converters, splits, corpus integrity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from agentic_graphrag.eval.public import UNVERIFIED
from agentic_graphrag.eval.public.converters import convert_dataset, load_raw
from agentic_graphrag.eval.public.corpus import (
    CUSTOM_SUBSET,
    UPSTREAM_SUBSET,
    IntegrityError,
    check_sources,
    corpus_sha256,
    file_sha256,
    freeze_splits,
    sample_ids,
    validate_corpus,
    validate_disjoint,
)
from agentic_graphrag.eval.public.enums import DatasetName, SplitName
from agentic_graphrag.eval.public.manifest import (
    RunManifest,
    load_manifest,
    manifest_hash,
    manifest_summary,
)
from agentic_graphrag.eval.public.records import BenchQuestion, Passage, read_models, write_jsonl

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "public_bench"
FIXTURE_MANIFEST = FIX / "manifest_fixture.yaml"


def _raw_manifest(path: Path = FIXTURE_MANIFEST) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("name", ["hipporag.yaml", "spike_b00.yaml"])
def test_committed_manifests_load_and_stay_unverified(name: str) -> None:
    manifest = load_manifest(ROOT / "configs" / "benchmarks" / name)
    assert manifest.protocol.status == UNVERIFIED
    assert manifest.protocol.upstream_revision is None
    assert not manifest.cache.answer_cache and not manifest.cache.completion_replay_cache
    assert manifest.prices == ()  # dated prices are supplied by the operator, never invented


def test_spike_manifest_matches_b00_row() -> None:
    m = load_manifest(ROOT / "configs" / "benchmarks" / "spike_b00.yaml")
    assert {d.name for d in m.datasets} == {DatasetName.HOTPOTQA, DatasetName.MUSIQUE}
    assert [a.name.value for a in m.arms] == ["pure_vector", "ag_single_tier"]
    assert len(m.run_seeds) == 1 and m.questions_per_dataset == 50
    assert m.spike_rules is not None


def test_manifest_hash_is_canonical_and_sensitive() -> None:
    raw = _raw_manifest()
    a = RunManifest.model_validate(raw)
    shuffled = dict(reversed(list(raw.items())))
    assert manifest_hash(a) == manifest_hash(RunManifest.model_validate(shuffled))
    raw["run_seeds"] = [11, 22, 44]
    assert manifest_hash(a) != manifest_hash(RunManifest.model_validate(raw))
    assert manifest_summary(a)["protocol_revision"] == UNVERIFIED


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"run_seeds": [5, 22, 33]}, "sampling seeds"),
        ({"run_seeds": [11, 11, 33]}, "duplicate run seed"),
        ({"cache": {"answer_cache": True}}, "replay caches"),
        ({"run_kind": "final"}, "final split"),
        ({"run_kind": "final", "split": "final", "run_seeds": [1, 2]}, "run seeds"),
        ({"split": "final"}, "final split"),
        ({"store_namespace": "default"}, "store_namespace"),
    ],
)
def test_bad_manifests_are_rejected(patch: dict[str, Any], message: str) -> None:
    raw = {**_raw_manifest(), **patch}
    with pytest.raises(ValidationError, match=message):
        RunManifest.model_validate(raw)


def test_ag_arm_register_flags_are_enforced() -> None:
    raw = _raw_manifest()
    raw["arms"] = [dict(a) for a in raw["arms"]]
    raw["arms"][-1]["register_enabled"] = False
    with pytest.raises(ValidationError, match="register_enabled"):
        RunManifest.model_validate(raw)


def test_final_requires_every_arm_and_dataset() -> None:
    raw = {**_raw_manifest(), "run_kind": "final", "split": "final"}
    raw["arms"] = raw["arms"][:2]
    with pytest.raises(ValidationError, match="missing"):
        RunManifest.model_validate(raw)


def test_hotpot_converter_preserves_ids_and_sentence_labels() -> None:
    conv = convert_dataset(FIX / "hotpotqa_dev.json", DatasetName.HOTPOTQA)
    q = conv.questions[0]
    assert q.question_id == "hp-001" and q.gold.answers == ("Lisbon",)
    harbor, duarte = q.gold.supporting_passage_ids
    assert conv.passages[duarte].title == "Maria Duarte"
    assert conv.passages[duarte].text.endswith("born in Lisbon in 1961.")
    assert q.gold.supporting_sentence_ids == (f"{harbor}#s0", f"{duarte}#s1")
    assert len(q.context_passage_ids) == 3 and not conv.issues


def test_2wiki_converter_keeps_aliases_and_evidence_as_gold_only() -> None:
    conv = convert_dataset(FIX / "2wikimultihopqa_dev.json", DatasetName.TWOWIKI)
    q = conv.questions[0]
    assert q.gold.answers == ("Henrik Sol", "H. Sol")
    assert q.gold.decomposition[0]["evidence"] == ["Glass River", "director", "Lena Sol"]
    # Shared Glass River passage dedupes across questions (content-addressed id).
    assert len(conv.passages) == 5
    assert "answers" not in q.to_input().model_dump()


def test_musique_converter_maps_supporting_paragraphs() -> None:
    conv = convert_dataset(FIX / "musique_dev.jsonl", DatasetName.MUSIQUE)
    q = conv.questions[0]
    assert q.question_id == "2hop__100_200" and q.gold.answers == ("Czech Republic", "Czechia")
    titles = [conv.passages[p].title for p in q.gold.supporting_passage_ids]
    assert titles == ["Petra Novak", "Charles University"]
    assert q.gold.question_type == "2hop" and len(q.gold.decomposition) == 2


def test_hipporag_corpus_rekeys_supporting_labels() -> None:
    conv = convert_dataset(
        FIX / "hipporag_hotpotqa.json",
        DatasetName.HOTPOTQA,
        corpus_path=FIX / "hipporag_hotpotqa_corpus.json",
    )
    assert len(conv.passages) == 6  # corpus is canonical, including distractor "Lisbon"
    castrel = conv.questions[1].gold.supporting_passage_ids[1]
    assert conv.passages[castrel].text.startswith("Castrel is the largest")
    assert conv.questions[1].gold.supporting_sentence_ids[1] == f"{castrel}#s1"
    assert not conv.issues


def test_rekey_reports_support_missing_from_corpus(tmp_path: Path) -> None:
    corpus = load_raw(FIX / "hipporag_hotpotqa_corpus.json")[:2]
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")
    conv = convert_dataset(FIX / "hipporag_hotpotqa.json", DatasetName.HOTPOTQA, corpus_path=path)
    assert any("hp-003" in issue for issue in conv.issues)
    assert conv.questions[1].gold.supporting_passage_ids == ()


def test_normalized_round_trip(tmp_path: Path) -> None:
    conv = convert_dataset(FIX / "musique_dev.jsonl", DatasetName.MUSIQUE)
    write_jsonl(tmp_path / "q.jsonl", conv.questions)
    write_jsonl(tmp_path / "p.jsonl", conv.passages.values())
    assert read_models(tmp_path / "q.jsonl", BenchQuestion) == conv.questions
    passages = read_models(tmp_path / "p.jsonl", Passage)
    assert corpus_sha256(passages) == corpus_sha256(conv.passages.values())


def test_sampling_is_deterministic_and_seed_dependent() -> None:
    ids = [f"q{i}" for i in range(100)]
    assert sample_ids(ids, 10, 3) == sample_ids(reversed(ids), 10, 3)
    assert sample_ids(ids, 10, 3) != sample_ids(ids, 10, 4)
    assert sample_ids(ids[:5], 10, 3) == sorted(ids[:5])


def test_freeze_splits_keeps_dev_splits_out_of_final() -> None:
    spec = load_manifest(FIXTURE_MANIFEST).dataset(DatasetName.HOTPOTQA)
    pool = [f"q{i}" for i in range(10)]
    frozen = freeze_splits(spec, pool, pool)
    final = set(frozen.get(SplitName.FINAL))
    assert len(final) == 2 and frozen.provenance == CUSTOM_SUBSET
    for split in (SplitName.SPIKE, SplitName.PILOT, SplitName.DEV):
        assert not final & set(frozen.get(split))
    upstream = freeze_splits(spec, pool, pool, upstream_final=["q1", "q2", "q3"])
    assert upstream.provenance == UPSTREAM_SUBSET and len(upstream.get(SplitName.FINAL)) == 3


def test_disjointness_validation_rejects_overlap() -> None:
    with pytest.raises(IntegrityError, match="q1"):
        validate_disjoint({SplitName.FINAL: ["q1", "q2"], SplitName.PILOT: ["q1"]})


def test_corpus_validation_checks_count_and_hash() -> None:
    conv = convert_dataset(FIX / "musique_dev.jsonl", DatasetName.MUSIQUE)
    passages = list(conv.passages.values())
    digest = validate_corpus(passages, expected_count=len(passages), expected_sha256=None)
    assert validate_corpus(passages, expected_count=None, expected_sha256=digest) == digest
    with pytest.raises(IntegrityError, match="passages"):
        validate_corpus(passages, expected_count=1, expected_sha256=None)
    with pytest.raises(IntegrityError, match="sha256"):
        validate_corpus(passages[1:], expected_count=None, expected_sha256=digest)


def test_source_allowlist_rejects_demo_unlisted_unpinned_and_mismatch(tmp_path: Path) -> None:
    manifest = load_manifest(FIXTURE_MANIFEST)
    spec = manifest.dataset(DatasetName.HOTPOTQA)
    demo = ROOT / "data" / "raw" / "anything.md"
    stray = tmp_path / "other.json"
    stray.write_text("[]", encoding="utf-8")
    problems = check_sources(spec, [demo, stray, FIX / "hotpotqa_dev.json"], ROOT)
    assert "developer/demo" in problems[0]
    assert "not in the hotpotqa source allowlist" in problems[1]
    assert "not pinned" in problems[2]
    pinned = spec.model_copy(update={"source_allowlist": {"hotpotqa_dev.json": "0" * 64}})
    assert "mismatch" in check_sources(pinned, [FIX / "hotpotqa_dev.json"], ROOT)[0]
    good = file_sha256(FIX / "hotpotqa_dev.json")
    ok = spec.model_copy(update={"source_allowlist": {"hotpotqa_dev.json": good}})
    assert check_sources(ok, [FIX / "hotpotqa_dev.json"], ROOT) == []
