"""Triple spot-check sample and scoring commands."""

from __future__ import annotations

import argparse
import json
import sys

from agentic_graphrag.config import resolve_path

_GATE_RATE = 0.70
_TARGET_PCT = 70.0


def spotcheck_main(argv: list[str] | None = None) -> None:
    """Generate triple spot-check sample for P1-KG-05 / G1→G2 live extract audit."""
    args = _parse_spotcheck(argv)
    from agentic_graphrag.config import resolve_path as rp
    from agentic_graphrag.knowledge.schema_check import Triple, load_schema, validate_triple

    triples_path = rp(args.triples)
    if not triples_path.exists():
        print(f"Triples file not found: {triples_path}", file=sys.stderr)
        sys.exit(2)
    schema = load_schema(rp(args.schema))
    out_path = rp(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _sample_rows(
        triples_path,
        schema,
        mode=args.mode,
        limit=args.limit,
        Triple=Triple,
        validate_triple=validate_triple,
    )
    _write_spotcheck(out_path, rows, mode=args.mode)


def _parse_spotcheck(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build triple spot-check sample")
    parser.add_argument("--triples", default="data/processed/seed_triples.jsonl")
    parser.add_argument("--out", default="reports/triple_spotcheck.jsonl")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--mode", choices=("seed", "llm"), default="seed")
    parser.add_argument("--schema", default="configs/schema/domain_v0.yaml")
    return parser.parse_args(argv)


def _sample_rows(triples_path, schema, *, mode, limit, Triple, validate_triple):
    rows = []
    for line in triples_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        t = Triple.model_validate(json.loads(line))
        reason = validate_triple(t, schema)
        label, label_source = _label_for(mode, reason)
        rows.append(
            {
                "head": t.head.model_dump(),
                "relation": t.relation,
                "tail": t.tail.model_dump(),
                "confidence": t.confidence,
                "source_span": t.source_span,
                "source_doc_id": t.source_doc_id,
                "source_chunk_id": t.source_chunk_id,
                "schema_ok": reason is None,
                "schema_reason": reason,
                "human_label": label,
                "label_source": label_source,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _label_for(mode: str, reason: str | None) -> tuple[str, str]:
    if mode == "seed":
        return (
            ("correct" if reason is None else "incorrect"),
            "seed_baseline_schema_valid",
        )
    if reason is not None:
        return "incorrect", "schema_reject"
    return "pending_human", "llm_extract_pending_human"


def _write_spotcheck(out_path, rows, *, mode: str) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = _build_spotcheck_summary(rows, out_path, mode=mode)
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _build_spotcheck_summary(rows, out_path, *, mode: str) -> dict:
    counts = _label_counts(rows)
    seed = mode == "seed"
    return {
        "mode": mode,
        "sample_size": len(rows),
        "correct": counts["correct"],
        "incorrect": counts["incorrect"],
        "pending_human": counts["pending"],
        "correct_rate": counts["rate"],
        "correct_rate_pct": counts["rate_pct"],
        "label_source": _label_source_note(seed),
        "target_correct_rate_pct": _TARGET_PCT,
        "note": _mode_note(seed),
        "path": str(out_path),
    }


def _label_counts(rows: list[dict]) -> dict:
    labeled = [r for r in rows if r.get("human_label") in ("correct", "incorrect")]
    correct = sum(1 for r in labeled if r["human_label"] == "correct")
    rate = (correct / len(labeled)) if labeled else None
    return {
        "correct": correct,
        "incorrect": len(labeled) - correct,
        "pending": sum(1 for r in rows if r.get("human_label") == "pending_human"),
        "rate": None if rate is None else round(rate, 4),
        "rate_pct": None if rate is None else round(rate * 100, 2),
        "labeled_n": len(labeled),
    }


def _label_source_note(seed: bool) -> str:
    if seed:
        return "seed_baseline (schema-valid seed triples treated as correct)"
    return "llm extract: set human_label correct|incorrect then re-score"


def _mode_note(seed: bool) -> str:
    if seed:
        return "P1-KG-05 seed baseline"
    return (
        "G1→G2 live extract audit: fill human_label, then: "
        "python -m agentic_graphrag score-spotcheck"
    )


def score_spotcheck_main(argv: list[str] | None = None) -> None:
    """Re-score a spotcheck JSONL after human labels are filled in."""
    parser = argparse.ArgumentParser(description="Score triple spot-check after human labeling")
    parser.add_argument("--in", dest="inp", default="reports/triple_spotcheck_llm.jsonl")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    inp = resolve_path(args.inp)
    if not inp.exists():
        print(f"Not found: {inp}", file=sys.stderr)
        sys.exit(2)
    rows = [
        json.loads(line) for line in inp.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    summary = _score_summary(rows, inp)
    out = resolve_path(args.out) if args.out else inp.with_suffix(".summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["pending_human"]:
        print(f"Warning: {summary['pending_human']} rows still pending_human", file=sys.stderr)
    rate = summary["correct_rate"]
    if rate is not None and rate < _GATE_RATE:
        print(f"Below 70% extract gate: {rate * 100:.1f}%", file=sys.stderr)
        sys.exit(3)


def _score_summary(rows: list[dict], inp) -> dict:
    counts = _label_counts(rows)
    rate = counts["rate"]
    pending_n = counts["pending"]
    return {
        "sample_size": len(rows),
        "labeled": counts["labeled_n"],
        "pending_human": pending_n,
        "correct": counts["correct"],
        "incorrect": counts["incorrect"],
        "correct_rate": rate,
        "correct_rate_pct": counts["rate_pct"],
        "pass_g1_extract_gate": bool(rate is not None and rate >= _GATE_RATE and pending_n == 0),
        "target_correct_rate_pct": _TARGET_PCT,
        "path": str(inp),
    }
