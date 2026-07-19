"""Triple spot-check sample and scoring commands."""

from __future__ import annotations

import argparse
import json
import sys

from agentic_graphrag.config import resolve_path


def spotcheck_main(argv: list[str] | None = None) -> None:
    """Generate triple spot-check sample for P1-KG-05 / G1→G2 live extract audit."""
    parser = argparse.ArgumentParser(description="Build triple spot-check sample")
    parser.add_argument("--triples", default="data/processed/seed_triples.jsonl")
    parser.add_argument("--out", default="reports/triple_spotcheck.jsonl")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--mode",
        choices=("seed", "llm"),
        default="seed",
        help="seed: schema-valid→correct (POC baseline); llm: pending_human for manual audit",
    )
    parser.add_argument(
        "--schema",
        default="configs/schema/domain_v0.yaml",
        help="Schema YAML path",
    )
    args = parser.parse_args(argv)
    from agentic_graphrag.config import resolve_path as rp
    from agentic_graphrag.knowledge.schema_check import Triple, load_schema, validate_triple

    triples_path = rp(args.triples)
    if not triples_path.exists():
        print(f"Triples file not found: {triples_path}", file=sys.stderr)
        sys.exit(2)
    schema = load_schema(rp(args.schema))
    out_path = rp(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for line in triples_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        t = Triple.model_validate(json.loads(line))
        reason = validate_triple(t, schema)
        if args.mode == "seed":
            # Seed triples are curated gold for interim corpus → label correct when schema-valid
            label = "correct" if reason is None else "incorrect"
            label_source = "seed_baseline_schema_valid"
        else:
            # LLM extract: leave for human audit; schema failure is already incorrect
            if reason is not None:
                label = "incorrect"
                label_source = "schema_reject"
            else:
                label = "pending_human"
                label_source = "llm_extract_pending_human"
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
        if len(rows) >= args.limit:
            break

    labeled = [r for r in rows if r["human_label"] in ("correct", "incorrect")]
    pending = sum(1 for r in rows if r["human_label"] == "pending_human")
    correct = sum(1 for r in labeled if r["human_label"] == "correct")
    rate = correct / len(labeled) if labeled else None
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "mode": args.mode,
        "sample_size": len(rows),
        "correct": correct,
        "incorrect": sum(1 for r in labeled if r["human_label"] == "incorrect"),
        "pending_human": pending,
        "correct_rate": round(rate, 4) if rate is not None else None,
        "correct_rate_pct": round(rate * 100, 2) if rate is not None else None,
        "label_source": (
            "seed_baseline (schema-valid seed triples treated as correct)"
            if args.mode == "seed"
            else "llm extract: set human_label correct|incorrect then re-score"
        ),
        "target_correct_rate_pct": 70.0,
        "note": (
            "P1-KG-05 seed baseline"
            if args.mode == "seed"
            else (
                "G1→G2 live extract audit: fill human_label, then: "
                "python -m agentic_graphrag score-spotcheck"
            )
        ),
        "path": str(out_path),
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

def score_spotcheck_main(argv: list[str] | None = None) -> None:
    """Re-score a spotcheck JSONL after human labels are filled in."""
    parser = argparse.ArgumentParser(description="Score triple spot-check after human labeling")
    parser.add_argument("--in", dest="inp", default="reports/triple_spotcheck_llm.jsonl")
    parser.add_argument(
        "--out", default=None, help="Summary JSON path (default: <in>.summary.json)"
    )
    args = parser.parse_args(argv)
    inp = resolve_path(args.inp)
    if not inp.exists():
        print(f"Not found: {inp}", file=sys.stderr)
        sys.exit(2)
    rows = [
        json.loads(line) for line in inp.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    pending = [r for r in rows if r.get("human_label") == "pending_human"]
    labeled = [r for r in rows if r.get("human_label") in ("correct", "incorrect")]
    correct = sum(1 for r in labeled if r["human_label"] == "correct")
    rate = correct / len(labeled) if labeled else None
    summary = {
        "sample_size": len(rows),
        "labeled": len(labeled),
        "pending_human": len(pending),
        "correct": correct,
        "incorrect": len(labeled) - correct,
        "correct_rate": round(rate, 4) if rate is not None else None,
        "correct_rate_pct": round(rate * 100, 2) if rate is not None else None,
        "pass_g1_extract_gate": bool(rate is not None and rate >= 0.70 and not pending),
        "target_correct_rate_pct": 70.0,
        "path": str(inp),
    }
    out = resolve_path(args.out) if args.out else inp.with_suffix(".summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if pending:
        print(f"Warning: {len(pending)} rows still pending_human", file=sys.stderr)
    if rate is not None and rate < 0.70:
        print(f"Below 70% extract gate: {rate * 100:.1f}%", file=sys.stderr)
        sys.exit(3)

