"""Trial planning: run seeds, B-12 step-down scale and balanced block order (B-08, B-12)."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentic_graphrag.eval.public.corpus import sample_ids
from agentic_graphrag.eval.public.enums import AG_PAIR, ArmName, DatasetName, StepDownLevel
from agentic_graphrag.eval.public.manifest import RunManifest, manifest_hash
from agentic_graphrag.eval.public.manifest_models import ArmSpec
from agentic_graphrag.eval.public.trials import TrialKey

STEP_DOWN_LABELS: dict[StepDownLevel, str] = {
    StepDownLevel.FULL: "full scale (all arms x all run seeds x frozen questions)",
    StepDownLevel.AG_AND_STOCHASTIC_SEEDS: (
        "B-12 level 1: >=3 seeds only for the AG pair and stochastic arms; "
        "deterministic arms 1 seed + repeat subset"
    ),
    StepDownLevel.ONE_SEED_NON_AG: "B-12 level 2: one seed for every non-AG arm",
    StepDownLevel.HALF_QUESTIONS: "B-12 level 3: level 2 + reduced questions per dataset",
}


def seeds_for(manifest: RunManifest, arm: ArmSpec, level: StepDownLevel) -> tuple[int, ...]:
    seeds = manifest.run_seeds
    if arm.name in AG_PAIR or level is StepDownLevel.FULL:
        return seeds
    if level is StepDownLevel.AG_AND_STOCHASTIC_SEEDS and arm.stochastic:
        return seeds
    return seeds[:1]


def questions_for(
    manifest: RunManifest, dataset: DatasetName, ids: Sequence[str], level: StepDownLevel
) -> list[str]:
    frozen = sorted(ids)
    if level is StepDownLevel.HALF_QUESTIONS:
        seed = manifest.dataset(dataset).sampling_seed
        return sample_ids(frozen, manifest.ceiling.reduced_questions, seed)
    return frozen


def _repeat_keys(
    manifest: RunManifest, arm: ArmSpec, dataset: DatasetName, questions: list[str]
) -> list[tuple[int, list[str]]]:
    """Level 1: deterministic arms re-run a seeded subset on the remaining seeds."""
    if arm.name in AG_PAIR or arm.stochastic:
        return []
    subset = sample_ids(
        questions,
        manifest.ceiling.deterministic_repeat_questions,
        manifest.dataset(dataset).sampling_seed,
    )
    return [(seed, subset) for seed in manifest.run_seeds[1:]]


def plan_trials(
    manifest: RunManifest,
    question_ids: Mapping[DatasetName, Sequence[str]],
    *,
    level: StepDownLevel | None = None,
) -> list[TrialKey]:
    """Every expected trial at the chosen scale (default: the manifest's level)."""
    lvl = manifest.ceiling.step_down_level if level is None else level
    digest = manifest_hash(manifest)
    keys: list[TrialKey] = []
    for spec in manifest.datasets:
        questions = questions_for(manifest, spec.name, question_ids.get(spec.name, ()), lvl)
        for arm in manifest.arms:
            plan = [(seed, questions) for seed in seeds_for(manifest, arm, lvl)]
            if lvl is StepDownLevel.AG_AND_STOCHASTIC_SEEDS:
                plan += _repeat_keys(manifest, arm, spec.name, questions)
            keys.extend(
                TrialKey(
                    manifest_hash=digest,
                    arm=arm.name,
                    dataset=spec.name,
                    run_seed=seed,
                    question_id=qid,
                )
                for seed, qids in plan
                for qid in qids
            )
    return keys


@dataclass(frozen=True)
class Block:
    """One arm's slice of a question block; blocks of different arms interleave."""

    arm: ArmName
    dataset: DatasetName
    run_seed: int
    index: int
    keys: tuple[TrialKey, ...]


def execution_order(manifest: RunManifest, keys: Sequence[TrialKey]) -> list[Block]:
    """Randomized blocks: per (dataset, seed, question block) the arm order is shuffled."""
    grouped: dict[tuple[DatasetName, int], dict[ArmName, list[TrialKey]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for key in keys:
        grouped[(key.dataset, key.run_seed)][key.arm].append(key)
    size = manifest.execution.block_size
    blocks: list[Block] = []
    for (dataset, seed), by_arm in grouped.items():
        n_blocks = max((len(v) + size - 1) // size for v in by_arm.values())
        for b in range(n_blocks):
            rng = random.Random(f"{manifest.execution.order_seed}:{dataset}:{seed}:{b}")
            arms = sorted(by_arm, key=lambda a: a.value)
            rng.shuffle(arms)
            for arm in arms:
                chunk = tuple(by_arm[arm][b * size : (b + 1) * size])
                if chunk:
                    blocks.append(Block(arm, dataset, seed, b, chunk))
    return blocks
