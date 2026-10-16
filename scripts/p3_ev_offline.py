#!/usr/bin/env python3
"""P3-EV-01/02/03 offline regression pack (heldout + triage + incremental).

Usage:
  PYTHONPATH=src .venv/bin/python scripts/p3_ev_offline.py
  PYTHONPATH=src .venv/bin/python scripts/p3_ev_offline.py --skip-runs  # score only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
if not PY.exists():
    PY = Path(sys.executable)

CASES_HELD = "evals/datasets/g2_heldout.jsonl"
SEED = "data/processed/pilot_triples.jsonl"
OUT = ROOT / "reports" / "g3_offline"


def main() -> None:
    args = _parse()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.skip_runs:
        _run_heldout_suite()
        _run_triage_ablation()
    _write_reports()
    print(f"P3-EV offline pack → {OUT}")


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="P3-EV offline heldout/triage/incremental")
    p.add_argument(
        "--skip-runs", action="store_true", help="Only assemble scores from existing run artifacts"
    )
    return p.parse_args()


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        "PYTHONPATH": str(ROOT / "src"),
    }
    subprocess.check_call(cmd, cwd=ROOT, env=env)


def _py_mod(*args: str) -> list[str]:
    return [str(PY), "-m", "agentic_graphrag", *args]


def _run_heldout_suite() -> None:
    """P3-EV-01: heldout agentic + baseline offline."""
    held_dir = OUT / "heldout"
    held_dir.mkdir(parents=True, exist_ok=True)
    _run(
        _py_mod(
            "run-cases",
            "--no-llm",
            "--memory-graph",
            "--cases",
            CASES_HELD,
            "--seed-triples",
            SEED,
            "--out",
            str(held_dir),
            "--run-name",
            "agentic_run",
            "--force-agentic",
        )
    )
    _run(
        _py_mod(
            "run-baseline",
            "--no-llm",
            "--cases",
            CASES_HELD,
            "--chunks",
            "data/processed/pilot_chunks.jsonl",
            "--out",
            str(held_dir),
        )
    )


def _run_triage_ablation() -> None:
    """P3-EV-02: same heldout with triage on vs force agentic."""
    tri_dir = OUT / "triage"
    tri_dir.mkdir(parents=True, exist_ok=True)
    _run(
        _py_mod(
            "run-cases",
            "--no-llm",
            "--memory-graph",
            "--cases",
            CASES_HELD,
            "--seed-triples",
            SEED,
            "--out",
            str(tri_dir),
            "--run-name",
            "triage_on",
            "--enable-triage",
        )
    )
    _run(
        _py_mod(
            "run-cases",
            "--no-llm",
            "--memory-graph",
            "--cases",
            CASES_HELD,
            "--seed-triples",
            SEED,
            "--out",
            str(tri_dir),
            "--run-name",
            "force_agentic",
            "--force-agentic",
        )
    )


def _write_reports() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from agentic_graphrag.eval.p3_ev import (
        assemble_g3_scaffold,
        build_heldout_report,
        build_triage_report,
        route_histogram,
        run_incremental_drill,
        score_run,
        write_json,
    )

    cases = ROOT / CASES_HELD
    agentic_path = OUT / "heldout" / "agentic_run.jsonl"
    baseline_path = OUT / "heldout" / "baseline_run.jsonl"
    heldout = None
    if agentic_path.exists():
        ag = score_run(agentic_path, system="agentic", cases_path=cases)
        bl = (
            score_run(baseline_path, system="baseline", cases_path=cases)
            if baseline_path.exists()
            else None
        )
        heldout = build_heldout_report(agentic=ag, baseline=bl)
        write_json(OUT / "heldout_eval.json", heldout)

    triage = None
    ton = OUT / "triage" / "triage_on.jsonl"
    fag = OUT / "triage" / "force_agentic.jsonl"
    if ton.exists() and fag.exists():
        triage = build_triage_report(
            triage_on=score_run(ton, system="triage_on", cases_path=cases),
            force_agentic=score_run(fag, system="force_agentic", cases_path=cases),
            route_counts=route_histogram(ton),
        )
        write_json(OUT / "triage_ablation.json", triage)

    inc = run_incremental_drill().to_dict()
    write_json(OUT / "incremental_drill.json", inc)

    perf = ROOT / "reports" / "p3_perf_guardrails.json"
    g3 = assemble_g3_scaffold(
        heldout=heldout,
        triage=triage,
        incremental=inc,
        perf_path=perf if perf.exists() else None,
    )
    write_json(OUT / "G3_review_scaffold.json", g3)
    _write_g3_md(g3, heldout, triage, inc)


def _write_g3_md(
    g3: dict,
    heldout: dict | None,
    triage: dict | None,
    inc: dict,
) -> None:
    lines = [
        "# G3 评审材料脚手架（offline）",
        "",
        f"**状态：** `{g3['status']}` — **formal_g3_go={g3['formal_g3_go']}**",
        "",
        "本包为 **P3-EV offline** 产物，不构成 live AC-1/2/4 正式关闭。",
        "",
        "## P3-EV-01 Heldout",
        "",
    ]
    if heldout:
        a = heldout["agentic"]
        lines.append(f"- Agentic accuracy: **{a['accuracy_pct']}%** (n={a['n']})")
        lines.append(f"- Evidence recall: **{a.get('evidence_recall')}**")
        lines.append(f"- Δ vs baseline: **{heldout.get('delta_accuracy_pp')} pp**")
        lines.append(f"- gates: `{heldout['gates']}`")
    else:
        lines.append("- *(no heldout run artifacts)*")
    lines.extend(["", "## P3-EV-02 Triage ablation", ""])
    if triage:
        lines.append(f"- accuracy loss (agentic − triage): **{triage['accuracy_loss_pp']} pp**")
        lines.append(f"- route counts: `{triage.get('route_counts')}`")
        lines.append(f"- gates: `{triage['gates']}`")
    else:
        lines.append("- *(no triage artifacts)*")
    lines.extend(
        [
            "",
            "## Incremental drill (AC-5 smoke)",
            "",
            f"- accepted: {inc.get('batch_accepted')}",
            f"- conflicts auto/review: {inc.get('conflicts_auto')}/{inc.get('conflicts_review')}",
            f"- post_query_ok: {inc.get('post_query_ok')}",
            "",
            "## Open items",
            "",
        ]
    )
    for item in g3.get("open_items") or []:
        lines.append(f"- {item}")
    lines.append("")
    (OUT / "G3_review_scaffold.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
