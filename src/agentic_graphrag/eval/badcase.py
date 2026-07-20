"""Badcase attribution for P2-EV-05 (four primary buckets)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_graphrag.eval.scoring import score_pair

ATTRIBUTIONS = (
    "graph_missing",
    "retrieval",
    "decomposition",
    "generation",
    "correct",
    "gold_error",
)

_COVERAGE_THRESHOLD = 0.34
_PRED_PREVIEW = 300
_GOLD_PREVIEW = 200
_NO_ANSWER_GOLDS = frozenset({"", "no answer", "n/a", "none", "unknown", "无法回答"})


def _blob(row: dict[str, Any]) -> str:
    parts: list[str] = [str(row.get("prediction") or "")]
    chain = row.get("chain") or {}
    if isinstance(chain, dict):
        parts.extend(_step_blob(chain))
        parts.extend(_claim_blob(chain))
        parts.extend(str(x) for x in (chain.get("explored_paths") or []))
    return " ".join(parts).lower()


def _step_blob(chain: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for step in chain.get("steps") or []:
        if isinstance(step, dict):
            parts.extend(_one_step(step))
    return parts


def _one_step(step: dict[str, Any]) -> list[str]:
    parts = [str(step.get("sub_question") or ""), str(step.get("conclusion") or "")]
    for tc in step.get("tool_calls") or []:
        if isinstance(tc, dict):
            parts.extend(str(x) for x in (tc.get("hits") or []))
            parts.append(str(tc.get("tool") or ""))
    return parts


def _claim_blob(chain: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for claim in chain.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        parts.append(str(claim.get("text") or ""))
        parts.extend(str(x) for x in (claim.get("evidence_ids") or []))
    return parts


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


@dataclass
class _AttrCtx:
    cid: str
    case: dict
    gold: str
    pred: str
    scored: dict
    correct: bool
    status: str
    blob: str
    gold_tokens: list[str]
    evidence_coverage: float | None
    n_steps: int
    has_graph_tool: bool
    guardrail: str
    unbound: bool
    gold_is_no: bool


def attribute_row(
    row: dict[str, Any],
    cases_by_id: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Return attribution record for one run row."""
    cases_by_id = cases_by_id or {}
    ctx = _row_context(row, cases_by_id)
    attr, reason = _decide_attr(ctx)
    return {
        "case_id": ctx.cid,
        "correct": ctx.correct,
        "attribution": attr,
        "reason": reason,
        "score_method": ctx.scored.get("method"),
        "evidence_coverage": ctx.evidence_coverage,
        "n_steps": ctx.n_steps,
        "gold": ctx.gold[:_GOLD_PREVIEW],
        "prediction": ctx.pred[:_PRED_PREVIEW],
        "hops": ctx.case.get("hops") or row.get("hops"),
    }


def _row_context(row: dict[str, Any], cases_by_id: dict[str, dict]) -> _AttrCtx:
    cid = str(row.get("case_id") or row.get("id") or "")
    case = cases_by_id.get(cid, {})
    gold = str(row.get("gold") or row.get("gold_answer") or case.get("gold_answer") or "")
    pred = str(row.get("prediction") or "")
    scored = score_pair(pred, gold)
    blob = _blob(row)
    gold_tokens = _gold_tokens(row, cases_by_id)
    chain = row.get("chain") or {}
    steps, claims = _chain_lists(chain)
    return _AttrCtx(
        cid=cid,
        case=case,
        gold=gold,
        pred=pred,
        scored=scored,
        correct=bool(scored.get("correct")),
        status=str(row.get("status") or "").lower(),
        blob=blob,
        gold_tokens=gold_tokens,
        evidence_coverage=_coverage(gold_tokens, blob),
        n_steps=len(steps),
        has_graph_tool=_has_graph_tool(blob),
        guardrail=_guardrail_text(chain, row),
        unbound=_unbound_claims(claims),
        gold_is_no=gold.strip().lower() in _NO_ANSWER_GOLDS,
    )


def _chain_lists(chain: Any) -> tuple[list, list]:
    if not isinstance(chain, dict):
        return [], []
    return list(chain.get("steps") or []), list(chain.get("claims") or [])


def _coverage(gold_tokens: list[str], blob: str) -> float | None:
    if not gold_tokens:
        return None
    hits = sum(1 for tok in gold_tokens if (t := tok.lower().strip()) and t in blob)
    return hits / len(gold_tokens)


def _has_graph_tool(blob: str) -> bool:
    return "graph_" in blob or "graph_neighbor" in blob or "graph_path" in blob


def _guardrail_text(chain: Any, row: dict[str, Any]) -> str:
    meta = ""
    if isinstance(chain, dict):
        meta = str((chain.get("metadata") or {}).get("guardrail_status") or "")
    return meta + str(row.get("guardrail_status") or "")


def _unbound_claims(claims: list) -> bool:
    if not claims:
        return False
    return any(isinstance(c, dict) and not (c.get("evidence_ids") or []) for c in claims)


def _decide_attr(ctx: _AttrCtx) -> tuple[str, str]:
    if ctx.correct:
        return "correct", "answer matches gold"
    early = _early_attr(ctx)
    if early is not None:
        return early
    if ctx.gold_tokens:
        return _evidence_attr(ctx)
    if ctx.status in {"no_answer", "partial"} and not ctx.gold_is_no:
        return "retrieval", f"status={ctx.status} without gold match"
    return "generation", "default: incorrect answer"


def _early_attr(ctx: _AttrCtx) -> tuple[str, str] | None:
    if ctx.gold_is_no and ctx.pred.strip() and not ctx.pred.lower().startswith("无法"):
        return "generation", "should abstain (no-answer gold) but produced an answer"
    if ctx.n_steps == 0 or "give_up" in ctx.blob or "tripped" in ctx.guardrail.lower():
        return "decomposition", "no steps / give-up / guardrail trip"
    return None


def _evidence_attr(ctx: _AttrCtx) -> tuple[str, str]:
    cov = ctx.evidence_coverage
    if cov == 0.0 and not ctx.has_graph_tool:
        return "graph_missing", "no graph tool hits and gold evidence absent"
    if (cov or 0) < _COVERAGE_THRESHOLD:
        return "retrieval", f"gold evidence coverage {cov:.2f} < {_COVERAGE_THRESHOLD}"
    reason = "evidence partially present but answer incorrect"
    if ctx.unbound:
        reason += "; unbound claims"
    return "generation", reason


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
