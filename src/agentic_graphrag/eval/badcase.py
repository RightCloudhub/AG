"""Badcase attribution for P2-EV-05 (four primary buckets).

Categories (evaluation.md §6, collapsed for MVP):
- **graph_missing** — no answer / graph empty evidence when gold expects path
- **retrieval** — graph tools ran but gold evidence tokens absent from hits
- **decomposition** — plan/critic issues (0 steps, early give-up, hop trip)
- **generation** — evidence present but answer string wrong / uncited claims
"""

from __future__ import annotations

from typing import Any

from agentic_graphrag.eval.scoring import score_pair

ATTRIBUTIONS = (
    "graph_missing",
    "retrieval",
    "decomposition",
    "generation",
    "correct",
    "gold_error",  # reserved; not auto-assigned
)


def _blob(row: dict[str, Any]) -> str:
    parts: list[str] = [str(row.get("prediction") or "")]
    chain = row.get("chain") or {}
    if isinstance(chain, dict):
        for step in chain.get("steps") or []:
            if isinstance(step, dict):
                parts.append(str(step.get("sub_question") or ""))
                parts.append(str(step.get("conclusion") or ""))
                for tc in step.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        parts.extend(str(x) for x in (tc.get("hits") or []))
                        parts.append(str(tc.get("tool") or ""))
        for claim in chain.get("claims") or []:
            if isinstance(claim, dict):
                parts.append(str(claim.get("text") or ""))
                parts.extend(str(x) for x in (claim.get("evidence_ids") or []))
        parts.extend(str(x) for x in (chain.get("explored_paths") or []))
    return " ".join(parts).lower()


def _gold_tokens(row: dict[str, Any], cases_by_id: dict[str, dict]) -> list[str]:
    cid = str(row.get("case_id") or row.get("id") or "")
    case = cases_by_id.get(cid, {})
    items: list[str] = []
    for key in ("gold_evidence", "gold_path"):
        raw = case.get(key) or row.get(key)
        if isinstance(raw, list):
            items.extend(str(x) for x in raw if x)
        elif isinstance(raw, str) and raw.strip():
            items.append(raw.strip())
    return items


def attribute_row(
    row: dict[str, Any],
    cases_by_id: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Return attribution record for one run row."""
    cases_by_id = cases_by_id or {}
    cid = str(row.get("case_id") or row.get("id") or "")
    case = cases_by_id.get(cid, {})
    gold = str(row.get("gold") or row.get("gold_answer") or case.get("gold_answer") or "")
    pred = str(row.get("prediction") or "")
    scored = score_pair(pred, gold)
    correct = bool(scored.get("correct"))

    status = str(row.get("status") or "").lower()
    chain = row.get("chain") or {}
    steps = chain.get("steps") if isinstance(chain, dict) else None
    steps = steps or []
    blob = _blob(row)
    gold_tokens = _gold_tokens(row, cases_by_id)
    gold_is_no = gold.strip().lower() in {"", "no answer", "n/a", "none", "unknown", "无法回答"}

    evidence_hits = 0
    for tok in gold_tokens:
        t = tok.lower().strip()
        if t and t in blob:
            evidence_hits += 1
    evidence_coverage = (evidence_hits / len(gold_tokens)) if gold_tokens else None

    has_graph_tool = "graph_" in blob or "graph_neighbor" in blob or "graph_path" in blob
    n_steps = len(steps)
    guardrail = str(
        (chain.get("metadata") or {}).get("guardrail_status")
        if isinstance(chain, dict)
        else ""
    ) + str(row.get("guardrail_status") or "")
    claims = (chain.get("claims") if isinstance(chain, dict) else None) or []
    unbound = any(
        isinstance(c, dict) and not (c.get("evidence_ids") or []) for c in claims
    ) if claims else False

    if correct:
        attr = "correct"
        reason = "answer matches gold"
    elif gold_is_no and pred.strip() and not pred.lower().startswith("无法"):
        # Should have abstained
        attr = "generation"
        reason = "should abstain (no-answer gold) but produced an answer"
    elif n_steps == 0 or "give_up" in blob or "tripped" in guardrail.lower():
        attr = "decomposition"
        reason = "no steps / give-up / guardrail trip"
    elif gold_tokens and evidence_coverage == 0.0 and not has_graph_tool:
        attr = "graph_missing"
        reason = "no graph tool hits and gold evidence absent"
    elif gold_tokens and (evidence_coverage or 0) < 0.34:
        attr = "retrieval"
        reason = f"gold evidence coverage {evidence_coverage:.2f} < 0.34"
    elif gold_tokens and (evidence_coverage or 0) >= 0.34:
        attr = "generation"
        reason = "evidence partially present but answer incorrect"
        if unbound:
            reason += "; unbound claims"
    elif status in {"no_answer", "partial"} and not gold_is_no:
        attr = "retrieval"
        reason = f"status={status} without gold match"
    else:
        attr = "generation"
        reason = "default: incorrect answer"

    return {
        "case_id": cid,
        "correct": correct,
        "attribution": attr,
        "reason": reason,
        "score_method": scored.get("method"),
        "evidence_coverage": evidence_coverage,
        "n_steps": n_steps,
        "gold": gold[:200],
        "prediction": pred[:300],
        "hops": case.get("hops") or row.get("hops"),
    }


def attribute_run(
    rows: list[dict[str, Any]],
    cases_by_id: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Aggregate attributions for a full run."""
    items = [attribute_row(r, cases_by_id) for r in rows]
    counts: dict[str, int] = {a: 0 for a in ATTRIBUTIONS}
    for it in items:
        counts[it["attribution"]] = counts.get(it["attribution"], 0) + 1
    bad = [it for it in items if it["attribution"] != "correct"]
    return {
        "total": len(items),
        "correct": counts.get("correct", 0),
        "bad": len(bad),
        "by_attribution": counts,
        "badcases": bad,
    }
