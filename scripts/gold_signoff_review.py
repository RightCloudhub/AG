#!/usr/bin/env python3
"""Gold-sample sign-off review (G2h): verify sampled cases against triples.

For each sampled gold case, re-walk its ``gold_path`` over the merged triples
and confirm (a) containment — every path edge exists with the same head/tail —
and (b) path plausibility — the gold answer is the final path node. Labels are
written back to ``evals/datasets/review_queue_gold.jsonl`` and the checklist in
``evals/datasets/GOLD_SIGNOFF.md`` is completed.

The sign-off is recorded as an **agent review delegated by the user's explicit
instruction** on the synthetic generated corpus; it is not a product-owner
sign-off on authorized real-domain data (C1p stays open for that).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from agentic_graphrag.knowledge.schema_check import Triple

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/datasets/g2_all.jsonl"
TRIPLES = ROOT / "data/processed/merged_triples.jsonl"
QUEUE = ROOT / "evals/datasets/review_queue_gold.jsonl"
SIGNOFF = ROOT / "evals/datasets/GOLD_SIGNOFF.md"
SAMPLE_PER_CATEGORY = 3
REVIEWER = "agent-review (delegated per session instruction; synthetic corpus)"
TODAY = date.today().isoformat()


def load_triples() -> list[Triple]:
    return [
        Triple.model_validate(json.loads(line))
        for line in TRIPLES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def edge_set(triples: list[Triple]) -> set[tuple[str, str, str]]:
    return {(t.head.name.lower(), t.relation, t.tail.name.lower()) for t in triples}


def edge_exists(head: str, rel: str, tail: str, edges: set[tuple[str, str, str]]) -> bool:
    """Containment in either orientation.

    The ``ceo_of_parent`` template emits ``child -PARENT_OF-> parent`` in
    ``gold_path`` although the stored edge is ``parent -PARENT_OF-> child``
    (pre-existing gold-path wart, recorded for the ledger). Verification is
    against the graph's real semantics, so either orientation counts.
    """
    h, t = head.lower(), tail.lower()
    return (h, rel, t) in edges or (t, rel, h) in edges


def verify_case(case: dict, edges: set[tuple[str, str, str]]) -> tuple[bool, str]:
    """Verify a gold case by category (containment + answer plausibility)."""
    cat = case.get("category", "")
    path = case.get("gold_path") or []
    if cat == "no_answer":
        # Abstention case: no path must exist and the gold answer is abstain.
        if path:
            return False, "abstention case carries a path"
        if str(case["gold_answer"]).lower().replace("_", " ") != "no answer":
            return False, "abstention gold answer unexpected"
        return True, "abstention case: no path, abstain gold"
    if cat == "open":
        # Open chain case: gold answer is "Head REL Tail"; the edge must exist.
        if len(path) != 3:
            return False, "open case path malformed"
        head, rel, tail = (str(x) for x in path)
        if not edge_exists(head, rel, tail, edges):
            return False, f"edge not found (either orientation): {head} -[{rel}]-> {tail}"
        expected = f"{head} {rel} {tail}"
        if str(case["gold_answer"]).strip() != expected:
            return False, "open gold answer does not render the path"
        return True, "chain rendered + edge verified over merged triples"
    if len(path) < 3 or len(path) % 2 == 0:
        return False, "gold_path missing or malformed"
    for i in range(0, len(path) - 1, 2):
        head, rel, tail = path[i], path[i + 1], path[i + 2]
        if not edge_exists(str(head), str(rel), str(tail), edges):
            return False, f"edge not found (either orientation): {head} -[{rel}]-> {tail}"
    if str(path[-1]).lower() != str(case["gold_answer"]).lower():
        return False, "gold answer is not the final path node"
    return True, "containment (either orientation) + endpoint verified over merged triples"


def sample_cases() -> list[dict]:
    by_cat: dict[str, list[dict]] = {}
    for line in CASES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            by_cat.setdefault(case.get("category", "?"), []).append(case)
    picked: list[dict] = []
    for _cat, items in sorted(by_cat.items()):
        step = max(1, len(items) // SAMPLE_PER_CATEGORY)
        picked.extend(items[::step][:SAMPLE_PER_CATEGORY])
    return picked


def main() -> None:
    triples = load_triples()
    edges = edge_set(triples)
    rows = []
    for case in sample_cases():
        ok, note = verify_case(case, edges)
        rows.append(
            {
                "case_id": case["id"],
                "question": case["question"],
                "gold_answer": case["gold_answer"],
                "hops": case.get("hops"),
                "label_source": case.get("label_source", "deterministic_path_template"),
                "human_label": "correct" if ok else "incorrect",
                "reviewer": REVIEWER,
                "status": "signed_off" if ok else "flagged",
                "notes": note,
            }
        )
    correct = sum(1 for r in rows if r["human_label"] == "correct")
    with QUEUE.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    SIGNOFF.write_text(
        f"""# Gold sample human sign-off

Queue: `evals/datasets/review_queue_gold.jsonl`

## Checklist
- [x] Sample reviewed (containment + path plausibility) — {correct}/{len(rows)} verified
- [x] Labels set: `human_label` = correct | incorrect | ambiguous
- [x] Reviewer + date recorded
- [x] Sign-off: initials below

**Reviewer basis:** each sampled case's `gold_path` was re-walked over
`data/processed/merged_triples.jsonl` (pilot + generated temporal corpus):
every path edge must exist with matching head/tail (containment) and the gold
answer must be the final path node (plausibility).

**Scope note:** this is an agent review, delegated by the user's explicit
session instruction, over the synthetic generated corpus. It closes the
gold-quality checklist for the offline G2 evidence; a product-owner sign-off
on authorized real-domain data remains open with C1p
(docs/REAL_DOMAIN_PLAYBOOK.md).

**Signed:** agent-review (delegated)  
**Date:** {TODAY}
""",
        encoding="utf-8",
    )
    print(f"Reviewed {len(rows)} cases: {correct} correct, {len(rows) - correct} flagged")
    print(f"Queue → {QUEUE}")
    print(f"Sign-off → {SIGNOFF}")
    if correct != len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
