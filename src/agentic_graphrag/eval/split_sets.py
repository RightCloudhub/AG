"""Split gold cases into dev / heldout / guardrail (P2-EV-02 / R7).

Policy (evaluation.md §2):
- **dev** (~75%): daily tuning; badcase recirculation allowed
- **heldout** (~25%): gate-only; not used for iterative prompt tuning
- **guardrail**: AC-6 special set; not counted in ≥200 gold total
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from agentic_graphrag.eval.cases import EvalCase, dump_cases, load_cases


def _stable_bucket(case_id: str, *, heldout_ratio: float = 0.25) -> str:
    """Deterministic hash split — same id always lands in the same set."""
    digest = hashlib.sha1(case_id.encode("utf-8")).hexdigest()
    # first 8 hex → int in [0, 1)
    frac = int(digest[:8], 16) / 0xFFFFFFFF
    return "heldout" if frac < heldout_ratio else "dev"


def split_gold_cases(
    cases: list[EvalCase],
    *,
    heldout_ratio: float = 0.25,
    guardrail: list[EvalCase] | None = None,
) -> dict[str, list[EvalCase]]:
    """Return ``{"dev", "heldout", "guardrail", "all_gold"}`` lists."""
    dev: list[EvalCase] = []
    heldout: list[EvalCase] = []
    for c in cases:
        # force no_answer mix into both buckets via hash too
        if _stable_bucket(c.id, heldout_ratio=heldout_ratio) == "heldout":
            heldout.append(
                c.model_copy(update={"metadata": {**(c.metadata or {}), "split": "heldout"}})
            )
        else:
            dev.append(c.model_copy(update={"metadata": {**(c.metadata or {}), "split": "dev"}}))

    gr = guardrail or []
    gr_out = [
        c.model_copy(update={"metadata": {**(c.metadata or {}), "split": "guardrail"}}) for c in gr
    ]
    return {
        "dev": dev,
        "heldout": heldout,
        "guardrail": gr_out,
        "all_gold": cases,
    }


def write_split_datasets(
    cases: list[EvalCase],
    out_dir: str | Path,
    *,
    heldout_ratio: float = 0.25,
    guardrail: list[EvalCase] | None = None,
    stem: str = "g2",
) -> dict[str, Path]:
    """Write all.jsonl, dev.jsonl, heldout.jsonl, guardrail.jsonl under out_dir."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    splits = split_gold_cases(cases, heldout_ratio=heldout_ratio, guardrail=guardrail)
    paths: dict[str, Path] = {}
    paths["all"] = dump_cases(splits["all_gold"], out / f"{stem}_all.jsonl")
    paths["dev"] = dump_cases(splits["dev"], out / f"{stem}_dev.jsonl")
    paths["heldout"] = dump_cases(splits["heldout"], out / f"{stem}_heldout.jsonl")
    paths["guardrail"] = dump_cases(splits["guardrail"], out / f"{stem}_guardrail.jsonl")
    # Convenience aliases used by evaluation.md
    paths["dev_alias"] = dump_cases(splits["dev"], out / "dev.jsonl")
    paths["heldout_alias"] = dump_cases(splits["heldout"], out / "heldout.jsonl")
    paths["guardrail_alias"] = dump_cases(splits["guardrail"], out / "guardrail.jsonl")
    return paths


def load_split(path: str | Path) -> list[EvalCase]:
    return load_cases(path)


def split_summary(splits: dict[str, list[EvalCase]]) -> dict[str, object]:
    def cats(items: list[EvalCase]) -> dict[str, int]:
        d: dict[str, int] = {}
        for c in items:
            k = c.resolved_category().value
            d[k] = d.get(k, 0) + 1
        return d

    return {
        name: {"total": len(items), "by_category": cats(items)}
        for name, items in splits.items()
        if name != "all_gold" or True
    }
