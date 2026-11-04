"""Markdown rendering of the public benchmark report (B-09).

Missing data is printed as "Not Executed" (no results) or "unknown" (not measurable),
never as zero. Every table caption repeats the B-12 scale level.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentic_graphrag.eval.public import NOT_EXECUTED

UNKNOWN = "unknown"
DECIMALS = 3


def fmt(value: Any) -> str:
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{DECIMALS}f}"
    return str(value)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _known(block: dict[str, Any] | None) -> str:
    if not block:
        return UNKNOWN
    return f"{fmt(block.get('mean'))} (cov {fmt(block.get('coverage'))})"


def _cell_row(cell: dict[str, Any], recall_keys: Sequence[str]) -> list[str]:
    head = [cell["arm"], cell["dataset"], str(cell["seed"]), cell["status"]]
    if cell["status"] == NOT_EXECUTED:
        return head + [NOT_EXECUTED] * (9 + len(recall_keys))
    ev = cell["evidence"]
    cost = cell["cost"]
    selective = f"{fmt(cell['selective_em'])} (cov {fmt(cell['selective_coverage'])})"
    return head + [
        fmt(cell["em_squad"]),
        fmt(cell["f1_squad"]),
        fmt(cell["contain_normalized"]),
        selective,
        *(fmt(ev[k]["mean"]) for k in recall_keys),
        fmt(cost["latency_p50_ms"]),
        fmt(cost["latency_p95_ms"]),
        fmt(cost["timeout_rate"]),
        fmt(cost["error_rate"]),
        _known(cost["tokens_per_query"]),
    ]


def _cells_section(report: dict[str, Any], caption: str) -> list[str]:
    cells = report["cells"]
    executed = [c for c in cells if c["status"] != NOT_EXECUTED]
    recall_keys = list(executed[0]["evidence"]) if executed else []
    recall_keys = [k for k in recall_keys if k != "alignment_coverage"]
    header = [
        "arm", "dataset", "seed", "status", "EM", "F1", "contain", "selective EM (coverage)",
        *recall_keys, "P50 ms", "P95 ms", "timeout", "error", "tokens/q",
    ]  # fmt: skip
    rows = [_cell_row(c, recall_keys) for c in cells]
    return [f"## Per dataset × seed ({caption})", "", *_table(header, rows), ""]


def _macro_section(report: dict[str, Any], caption: str) -> list[str]:
    rows = [[m["arm"], *(fmt(v) for k, v in m.items() if k != "arm")] for m in report["macro"]]
    keys = [k for k in (report["macro"][0] if report["macro"] else {}) if k != "arm"]
    return [f"## Macro average over datasets ({caption})", "", *_table(["arm", *keys], rows), ""]


def _seed_section(report: dict[str, Any], caption: str) -> list[str]:
    rows: list[list[str]] = []
    for s in report["seed_summary"]:
        if s["status"] == NOT_EXECUTED:
            rows.append([s["arm"], s["dataset"], NOT_EXECUTED, NOT_EXECUTED, NOT_EXECUTED])
            continue
        f1 = s["f1"]
        clustered = f1["clustered"] or {}
        rows.append([
            s["arm"], s["dataset"], fmt(f1["across_seeds"]["mean"]),
            fmt(f1["across_seeds"]["stddev"]), fmt(clustered.get("ci")),
        ])  # fmt: skip
    header = ["arm", "dataset", "F1 mean over seeds", "F1 stddev", "F1 95% CI (question-clustered)"]
    return [f"## Seed variance and clustered intervals ({caption})", "", *_table(header, rows), ""]


def _paired_section(report: dict[str, Any], caption: str) -> list[str]:
    rows: list[list[str]] = []
    for p in report["paired_ag"]:
        if p["status"] == NOT_EXECUTED:
            rows.append([p["dataset"], NOT_EXECUTED, NOT_EXECUTED, NOT_EXECUTED])
            continue
        mc = "; ".join(
            f"seed {s}: +{r['b_only']}/-{r['a_only']} p={fmt(r['p_value'])}"
            for s, r in p["mcnemar_em_by_seed"].items()
        )
        f1 = p["f1_diff"] or {}
        lat = p["latency_ms_diff"] or {}
        rows.append([p["dataset"], mc, f"{fmt(f1.get('mean'))} {fmt(f1.get('ci'))}",
                     f"{fmt(lat.get('mean'))} {fmt(lat.get('ci'))}"])  # fmt: skip
    header = ["dataset", "McNemar EM per seed", "ΔF1 (registry − single)", "Δlatency ms"]
    return [f"## Paired AG: single-tier vs registry ({caption})", "", *_table(header, rows), ""]


def _misc_section(report: dict[str, Any], caption: str) -> list[str]:
    lines = [f"## Risk–coverage ({caption})", ""]
    for arm, value in report["risk_coverage"].items():
        shown = value if isinstance(value, str) else f"{len(value['curve'])} points"
        lines.append(f"- {arm}: {shown}")
    lines += ["", f"## Index construction cost ({caption})", ""]
    rows = [
        [c["arm"], c["dataset"], c["status"], _usage(c.get("usage"))] for c in report["index_costs"]
    ]
    lines += _table(["arm", "dataset", "status", "tokens / elapsed ms"], rows)
    lines += ["", "## Comparability deviations", ""]
    lines += [f"- {d}" for d in report["comparability_deviations"]]
    return lines + [""]


def _usage(usage: dict[str, Any] | None) -> str:
    if usage is None:
        return NOT_EXECUTED
    tokens = None
    if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
        tokens = usage["prompt_tokens"] + usage["completion_tokens"]
    return f"{fmt(tokens)} / {fmt(usage.get('elapsed_ms'))}"


def render_markdown(report: dict[str, Any]) -> str:
    m = report["manifest"]
    caption = f"scale: {report['step_down']['label']}"
    validation = report["journal_validation"]
    lines = [
        f"# Public benchmark report — {m['run_id']}",
        "",
        f"**Status: {report['status']}**",
        "",
        f"- Run kind: {m['run_kind']}; manifest sha256 `{m['manifest_sha256']}`",
        f"- Upstream protocol revision: {m['protocol_revision']}",
        f"- B-12 step-down level {report['step_down']['level']}: {report['step_down']['label']}",
        f"- Abstention detector: {report['abstention_detector']['version']}",
        f"- Journal validation: {fmt(validation) if validation else NOT_EXECUTED}",
        "",
    ]
    for section in (_cells_section, _macro_section, _seed_section, _paired_section, _misc_section):
        lines += section(report, caption)
    return "\n".join(lines)
