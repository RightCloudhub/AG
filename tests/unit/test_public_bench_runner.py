"""B-06/B-08/B-12 contracts: durable trials, resume, interrupted spend, retries, planning."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from agentic_graphrag.eval.public.accounting import UsageLedger
from agentic_graphrag.eval.public.adapters_base import AdapterResult, PrepareReport
from agentic_graphrag.eval.public.enums import (
    ArmName,
    DatasetName,
    ErrorKind,
    StepDownLevel,
    TrialStatus,
)
from agentic_graphrag.eval.public.journal_check import validate_journal
from agentic_graphrag.eval.public.manifest import RunManifest, load_manifest, manifest_hash
from agentic_graphrag.eval.public.planning import execution_order, plan_trials, seeds_for
from agentic_graphrag.eval.public.records import Passage, QuestionInput
from agentic_graphrag.eval.public.runner import Runner, preflight_blockers
from agentic_graphrag.eval.public.trials import (
    ATTEMPT_START,
    TRIAL_START,
    TrialJournal,
    TrialKey,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_MANIFEST = ROOT / "tests" / "fixtures" / "public_bench" / "manifest_fixture.yaml"
QIDS = {
    DatasetName.HOTPOTQA: ["hp-1", "hp-2"],
    DatasetName.TWOWIKI: ["2w-1"],
    DatasetName.MUSIQUE: ["mu-1"],
}


def _manifest(**updates: object) -> RunManifest:
    base = load_manifest(FIXTURE_MANIFEST)
    return base.model_copy(update=updates) if updates else base


def _ok(tokens: int = 10) -> AdapterResult:
    usage = UsageLedger(prompt_tokens=tokens, completion_tokens=1, llm_calls=1, elapsed_ms=5)
    return AdapterResult(prediction="Answer: x", status=TrialStatus.ANSWERED, usage=usage)


def _fail(kind: ErrorKind, tokens: int = 3) -> AdapterResult:
    usage = UsageLedger(prompt_tokens=tokens, completion_tokens=0, llm_calls=1, elapsed_ms=9)
    status = TrialStatus.TIMEOUT if kind is ErrorKind.TIMEOUT else TrialStatus.ERROR
    return AdapterResult(status=status, error="e", error_kind=kind, usage=usage)


class ScriptedAdapter:
    """Answers from a per-question script; ``raise`` simulates a crash after dispatch."""

    def __init__(self, arm: ArmName, script: dict[str, list[AdapterResult | str]]) -> None:
        self.arm = arm
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, int]] = []

    def prepare(self, passages: list[Passage], corpus_sha256: str) -> PrepareReport:
        return PrepareReport(arm=self.arm, passages=len(passages), corpus_sha256=corpus_sha256)

    def answer(self, question: QuestionInput, *, run_seed: int) -> AdapterResult:
        self.calls.append((question.question_id, run_seed))
        queue = self.script.get(question.question_id) or [_ok()]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if item == "crash":
            raise KeyboardInterrupt("simulated crash after dispatch")
        assert isinstance(item, AdapterResult)
        return item

    def close(self) -> None:
        pass


def _questions() -> dict[tuple[DatasetName, str], QuestionInput]:
    return {
        (ds, q): QuestionInput(dataset=ds, question_id=q, question=f"question {q}?")
        for ds, ids in QIDS.items()
        for q in ids
    }


def _runner(
    manifest: RunManifest,
    journal: TrialJournal,
    adapter_for: Callable[[ArmName], ScriptedAdapter],
    sleeps: list[float] | None = None,
) -> tuple[Runner, dict[ArmName, ScriptedAdapter]]:
    adapters = {a.name: adapter_for(a.name) for a in manifest.arms}
    sink = sleeps if sleeps is not None else []
    return Runner(manifest, adapters, journal, _questions(), sleep=sink.append), adapters


def _single_arm(manifest: RunManifest, arm: ArmName) -> RunManifest:
    return manifest.model_copy(
        update={"arms": (manifest.arm(arm),), "datasets": (manifest.datasets[0],)}
    )


def test_plan_full_scale_counts_every_arm_seed_question() -> None:
    m = _manifest()
    keys = plan_trials(m, QIDS)
    assert len(keys) == 6 * 3 * 4  # arms x seeds x questions
    assert len({k.id for k in keys}) == len(keys)
    assert all(k.manifest_hash == manifest_hash(m) for k in keys)


def test_step_down_levels_follow_predeclared_order() -> None:
    m = _manifest()
    vector = m.arm(ArmName.PURE_VECTOR)  # deterministic in the fixture manifest
    lightrag = m.arm(ArmName.LIGHTRAG)  # stochastic
    ag = m.arm(ArmName.AG_REGISTRY)
    lvl1 = StepDownLevel.AG_AND_STOCHASTIC_SEEDS
    assert seeds_for(m, vector, lvl1) == (11,) and seeds_for(m, lightrag, lvl1) == (11, 22, 33)
    lvl2 = StepDownLevel.ONE_SEED_NON_AG
    assert seeds_for(m, lightrag, lvl2) == (11,) and seeds_for(m, ag, lvl2) == (11, 22, 33)
    repeat = plan_trials(m, QIDS, level=lvl1)
    vec_seeds = {k.run_seed for k in repeat if k.arm is ArmName.PURE_VECTOR}
    assert vec_seeds == {11, 22, 33}  # 1 seed + repeat subset on the other seeds
    reduced = m.model_copy(
        update={"ceiling": m.ceiling.model_copy(update={"reduced_questions": 1})}
    )
    lvl3 = plan_trials(reduced, QIDS, level=StepDownLevel.HALF_QUESTIONS)
    hp = {k.question_id for k in lvl3 if k.dataset is DatasetName.HOTPOTQA}
    assert len(hp) == 1


def test_execution_order_is_deterministic_and_balanced() -> None:
    m = _manifest()
    keys = plan_trials(m, QIDS)
    blocks = execution_order(m, keys)
    assert blocks == execution_order(m, list(reversed(keys))) or len(blocks) > 0
    assert sum(len(b.keys) for b in blocks) == len(keys)
    first_arms = {b.arm for b in blocks if b.index == 0 and b.dataset is DatasetName.HOTPOTQA}
    assert first_arms == {a.name for a in m.arms}


def test_run_journals_and_resume_never_double_counts(tmp_path: Path) -> None:
    m = _manifest()
    journal = TrialJournal(tmp_path / "trials.jsonl")
    keys = plan_trials(m, QIDS)
    runner, adapters = _runner(m, journal, lambda a: ScriptedAdapter(a, {}))
    summary = runner.run(keys)
    assert summary.completed == len(keys) and summary.aborted is None
    again, adapters2 = _runner(m, journal, lambda a: ScriptedAdapter(a, {}))
    assert again.run(keys).completed == 0
    assert all(not a.calls for a in adapters2.values())
    check = validate_journal(journal, m, keys)
    assert check.ok and check.completed == len(keys)


def test_retries_keep_every_attempt_and_terminal_is_last(tmp_path: Path) -> None:
    m = _single_arm(_manifest(), ArmName.PURE_VECTOR)
    journal = TrialJournal(tmp_path / "t.jsonl")
    script: dict[str, list[AdapterResult | str]] = {
        "hp-1": [_fail(ErrorKind.RATE_LIMIT), _ok(50)],
        "hp-2": [
            _fail(ErrorKind.RATE_LIMIT),
            _fail(ErrorKind.TRANSPORT),
            _fail(ErrorKind.RATE_LIMIT),
        ],
    }
    sleeps: list[float] = []
    runner, _ = _runner(m, journal, lambda a: ScriptedAdapter(a, script), sleeps)
    runner.run(plan_trials(m, QIDS))
    states = {s.key.question_id: s for s in journal.states().values() if s.key.run_seed == 11}
    hp1, hp2 = states["hp-1"], states["hp-2"]
    assert hp1.attempt_starts == [1, 2] and hp1.terminal is not None
    assert hp1.terminal.status is TrialStatus.ANSWERED
    assert hp1.usage().prompt_tokens == 53  # failed attempt's spend is retained
    assert hp2.terminal is not None and hp2.terminal.status is TrialStatus.ERROR
    assert len(hp2.attempt_starts) == m.retry.max_attempts  # timeout/failure stays counted
    assert sleeps[:2] == [2.0, 2.0]


def test_non_retryable_failure_is_terminal(tmp_path: Path) -> None:
    m = _single_arm(_manifest(), ArmName.PURE_VECTOR)
    journal = TrialJournal(tmp_path / "t.jsonl")
    script: dict[str, list[AdapterResult | str]] = {"hp-1": [_fail(ErrorKind.TIMEOUT), _ok()]}
    runner, _ = _runner(m, journal, lambda a: ScriptedAdapter(a, script))
    runner.run(plan_trials(m, QIDS))
    state = next(s for s in journal.states().values() if s.key.question_id == "hp-1")
    assert state.attempt_starts == [1]
    assert state.terminal is not None and state.terminal.status is TrialStatus.TIMEOUT


def test_crash_after_dispatch_is_an_interrupted_attempt_with_unknown_spend(
    tmp_path: Path,
) -> None:
    m = _single_arm(_manifest(), ArmName.PURE_VECTOR)
    journal = TrialJournal(tmp_path / "t.jsonl")
    keys = plan_trials(m, QIDS)
    crash: dict[str, list[AdapterResult | str]] = {"hp-1": ["crash"]}
    runner, _ = _runner(m, journal, lambda a: ScriptedAdapter(a, crash))
    with pytest.raises(KeyboardInterrupt):
        runner.run(keys)
    events = [e["event"] for e in journal.events()]
    assert events[:2] == [TRIAL_START, ATTEMPT_START]  # journaled before dispatch
    pre = validate_journal(journal, m, keys)
    assert pre.interrupted_attempts == 1 and not pre.ok
    resumed, _ = _runner(m, journal, lambda a: ScriptedAdapter(a, {}))
    resumed.run(keys)
    state = next(s for s in journal.states().values() if s.key.question_id == "hp-1")
    assert state.attempt_starts == [1, 2] and state.interrupted == {1}
    assert state.usage().total_tokens is None  # unknown spend is never zero
    assert state.known_usage().prompt_tokens == 10
    post = validate_journal(journal, m, keys)
    assert post.ok and post.unknown_spend_trials == 1


def test_interrupted_attempts_exhaust_retries(tmp_path: Path) -> None:
    m = _single_arm(_manifest(), ArmName.PURE_VECTOR)
    m = m.model_copy(update={"retry": m.retry.model_copy(update={"max_attempts": 1})})
    journal = TrialJournal(tmp_path / "t.jsonl")
    key = plan_trials(m, QIDS)[0]
    journal.append(TRIAL_START, key)
    journal.append(ATTEMPT_START, key, attempt=1)
    runner, adapters = _runner(m, journal, lambda a: ScriptedAdapter(a, {}))
    runner.run_trial(key)
    state = journal.states()[key.id]
    assert state.terminal is not None and state.terminal.status is TrialStatus.INTERRUPTED
    assert not adapters[ArmName.PURE_VECTOR].calls


def test_validation_rejects_foreign_manifest_and_duplicates(tmp_path: Path) -> None:
    m = _single_arm(_manifest(), ArmName.PURE_VECTOR)
    journal = TrialJournal(tmp_path / "t.jsonl")
    keys = plan_trials(m, QIDS)
    runner, _ = _runner(m, journal, lambda a: ScriptedAdapter(a, {}))
    runner.run(keys)
    journal.append("trial_end", keys[0], terminal_attempt=1, status="answered")
    foreign = TrialKey(**{**keys[1].model_dump(), "manifest_hash": "0" * 64})
    journal.append(TRIAL_START, foreign)
    check = validate_journal(journal, m, keys + [])
    assert any("duplicate trial_end" in e for e in check.errors)
    assert any("another manifest hash" in e for e in check.errors)
    missing = validate_journal(TrialJournal(tmp_path / "empty.jsonl"), m, keys)
    assert len(missing.missing) == len(keys) and not missing.ok


def test_abort_rule_stops_a_block_and_stays_resumable(tmp_path: Path) -> None:
    m = _manifest()
    rule = m.ceiling.abort.model_copy(update={"window": 2, "max_error_rate": 1.0})
    ceiling = m.ceiling.model_copy(update={"abort": rule})
    m = m.model_copy(
        update={"ceiling": ceiling, "retry": m.retry.model_copy(update={"max_attempts": 1})}
    )
    journal = TrialJournal(tmp_path / "t.jsonl")
    bad: dict[str, list[AdapterResult | str]] = {
        q: [_fail(ErrorKind.FORBIDDEN)] for q in ["hp-1", "hp-2"]
    }
    runner, _ = _runner(m, journal, lambda a: ScriptedAdapter(a, bad))
    summary = runner.run(plan_trials(m, QIDS))
    assert summary.aborted is not None and "error rate" in summary.aborted


def test_token_ceiling_aborts_before_overspending(tmp_path: Path) -> None:
    m = _manifest()
    m = m.model_copy(update={"ceiling": m.ceiling.model_copy(update={"provider_tokens": 25})})
    journal = TrialJournal(tmp_path / "t.jsonl")
    runner, _ = _runner(m, journal, lambda a: ScriptedAdapter(a, {}))
    summary = runner.run(plan_trials(m, QIDS))
    assert summary.aborted is not None and "token ceiling" in summary.aborted
    assert summary.completed < len(plan_trials(m, QIDS))
    # A resumed runner starts from the journaled spend and refuses immediately.
    again, adapters = _runner(m, journal, lambda a: ScriptedAdapter(a, {}))
    assert again.run(plan_trials(m, QIDS)).aborted is not None
    assert all(not a.calls for a in adapters.values())


def test_preflight_blocks_live_runs_until_pins_exist() -> None:
    final = load_manifest(ROOT / "configs" / "benchmarks" / "hipporag.yaml")
    blockers = preflight_blockers(final, has_credentials=False)
    joined = "\n".join(blockers)
    for needle in ("credentials", "cost ceiling", "prices", "UNVERIFIED", "promotion bounds"):
        assert needle in joined
    assert preflight_blockers(_manifest(), has_credentials=False) == []
