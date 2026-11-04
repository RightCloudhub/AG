"""Corpus integrity, source allowlists and deterministic split freezing (B-02, B-03)."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from agentic_graphrag.eval.public.enums import SplitName
from agentic_graphrag.eval.public.manifest_models import DatasetSpec
from agentic_graphrag.eval.public.records import Passage

# Developer/demo data that must never feed a public corpus or be cleared by the runner.
FORBIDDEN_DATA_DIRS: tuple[str, ...] = (
    "data/raw",
    "data/processed",
    "data/indexes",
    "data/cache",
    "data/pilot",
    "evals/datasets",
)
DEV_SPLIT_ORDER: tuple[SplitName, ...] = (SplitName.SPIKE, SplitName.SMOKE, SplitName.PILOT)
UPSTREAM_SUBSET = "upstream_subset"
CUSTOM_SUBSET = "protocol_aligned_custom_subset"
_HASH_CHUNK = 1 << 20


class IntegrityError(ValueError):
    """A corpus, source or split failed a B-02/B-03 integrity check."""


def corpus_sha256(passages: Iterable[Passage]) -> str:
    """Order-independent digest over (passage_id, title, text)."""
    h = hashlib.sha256()
    for p in sorted(passages, key=lambda x: x.passage_id):
        line = json.dumps([p.passage_id, p.title, p.text], ensure_ascii=False)
        h.update(line.encode("utf-8") + b"\n")
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def is_forbidden_path(path: Path, repo_root: Path) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to((repo_root / d).resolve()) for d in FORBIDDEN_DATA_DIRS)


def check_sources(spec: DatasetSpec, paths: Iterable[Path], repo_root: Path) -> list[str]:
    """Return blocking problems: unlisted, unpinned, mismatched or demo-data sources."""
    problems: list[str] = []
    for path in paths:
        if is_forbidden_path(path, repo_root):
            problems.append(f"{path}: developer/demo data is not an allowed public source")
            continue
        if path.name not in spec.source_allowlist:
            problems.append(f"{path.name}: not in the {spec.name} source allowlist")
            continue
        pinned = spec.source_allowlist[path.name]
        if not path.exists():
            problems.append(f"{path}: missing (download it; Not Executed)")
        elif pinned is None:
            problems.append(f"{path.name}: sha256 not pinned (UNVERIFIED)")
        elif file_sha256(path) != pinned:
            problems.append(f"{path.name}: sha256 mismatch with the allowlist")
    return problems


def sample_ids(ids: Iterable[str], n: int, seed: int) -> list[str]:
    """Deterministic sample (sorted pool, seeded RNG); returned sorted for publication."""
    pool = sorted(set(ids))
    if n >= len(pool):
        return pool
    return sorted(random.Random(seed).sample(pool, n))


@dataclass(frozen=True)
class FrozenSplits:
    ids: Mapping[SplitName, tuple[str, ...]]
    provenance: str
    sampling_seed: int

    def get(self, split: SplitName) -> tuple[str, ...]:
        return self.ids.get(split, ())


def freeze_splits(
    spec: DatasetSpec,
    final_pool: Iterable[str],
    dev_pool: Iterable[str],
    *,
    upstream_final: Iterable[str] | None = None,
) -> FrozenSplits:
    """Final = upstream subset when supplied, else a seeded sample; dev splits exclude it."""
    if upstream_final is not None:
        final = sorted(set(upstream_final))
        provenance = UPSTREAM_SUBSET
    else:
        final = sample_ids(final_pool, spec.final_size, spec.sampling_seed)
        provenance = CUSTOM_SUBSET
    remaining = sorted(set(dev_pool) - set(final))
    ids: dict[SplitName, tuple[str, ...]] = {SplitName.FINAL: tuple(final)}
    for offset, split in enumerate(DEV_SPLIT_ORDER, start=1):
        chosen = sample_ids(
            remaining, spec.split_sizes.get(split.value, 0), spec.sampling_seed + offset
        )
        ids[split] = tuple(chosen)
        remaining = sorted(set(remaining) - set(chosen))
    ids[SplitName.DEV] = tuple(remaining)
    frozen = FrozenSplits(ids=ids, provenance=provenance, sampling_seed=spec.sampling_seed)
    validate_disjoint(frozen.ids)
    return frozen


def validate_disjoint(splits: Mapping[SplitName, Iterable[str]]) -> None:
    """Every non-final split must be disjoint from final and from each other."""
    seen: dict[str, SplitName] = {}
    for split, ids in splits.items():
        for qid in ids:
            other = seen.get(qid)
            if other is not None and other is not split:
                raise IntegrityError(f"question {qid} appears in both {other} and {split}")
            seen[qid] = split


def validate_corpus(
    passages: Iterable[Passage], *, expected_count: int | None, expected_sha256: str | None
) -> str:
    items = list(passages)
    if expected_count is not None and len(items) != expected_count:
        raise IntegrityError(f"corpus has {len(items)} passages, expected {expected_count}")
    digest = corpus_sha256(items)
    if expected_sha256 is not None and digest != expected_sha256:
        raise IntegrityError("corpus sha256 does not match the manifest")
    return digest
