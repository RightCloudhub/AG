"""Evidence-recall helpers for evaluation metrics."""

from __future__ import annotations

from typing import Any


def gold_evidence_items(row: dict[str, Any], cases_by_id: dict[str, dict]) -> list[str]:
    """Gold evidence tokens: gold_path nodes/relations or case gold_evidence."""
    cid = row.get("case_id") or row.get("id")
    case = cases_by_id.get(str(cid) if cid is not None else "", {})
    items: list[str] = []
    for key in ("gold_evidence", "gold_path"):
        items.extend(_as_str_list(case.get(key) or row.get(key)))
    return items


def _as_str_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def predicted_evidence_blob(row: dict[str, Any]) -> str:
    """Flatten chain evidence for recall matching.

    Deliberately excludes the final prediction text so answer wording alone
    cannot inflate evidence recall.
    """
    parts: list[str] = []
    chain = row.get("chain") or {}
    if isinstance(chain, dict):
        parts.extend(_chain_evidence_parts(chain))
    parts.extend(str(x) for x in (row.get("explored_paths") or []))
    return " ".join(parts).lower()


def _chain_evidence_parts(chain: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    parts.extend(_claim_parts(chain))
    parts.extend(_step_parts(chain))
    parts.extend(str(x) for x in (chain.get("explored_paths") or []))
    parts.extend(_catalog_parts(chain))
    return parts


def _catalog_parts(chain: dict[str, Any]) -> list[str]:
    meta = chain.get("metadata") if isinstance(chain.get("metadata"), dict) else {}
    catalog = chain.get("evidence") or (meta.get("evidence") if meta else None) or []
    parts: list[str] = []
    for ev in catalog:
        if isinstance(ev, dict):
            parts.append(str(ev.get("id") or ""))
            parts.append(str(ev.get("content") or ""))
        else:
            parts.append(str(ev))
    return parts


def _claim_parts(chain: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for claim in chain.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        parts.append(str(claim.get("text") or ""))
        parts.extend(str(x) for x in (claim.get("evidence_ids") or []))
    return parts


def _step_parts(chain: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for step in chain.get("steps") or []:
        if not isinstance(step, dict):
            continue
        parts.extend(_one_step_parts(step))
    return parts


def _one_step_parts(step: dict[str, Any]) -> list[str]:
    parts = [
        str(step.get("conclusion") or ""),
        str(step.get("sub_question") or ""),
    ]
    parts.extend(str(x) for x in (step.get("evidence_ids") or []))
    for tc in step.get("tool_calls") or []:
        if isinstance(tc, dict):
            parts.extend(str(x) for x in (tc.get("hits") or []))
    return parts


def evidence_recall_for_row(
    row: dict[str, Any],
    cases_by_id: dict[str, dict],
    *,
    min_hops: int = 2,
) -> float | None:
    """Fraction of gold evidence items mentioned in the chain (AC-2 style)."""
    if _skip_for_hops(row, cases_by_id, min_hops=min_hops):
        return None
    gold_items = gold_evidence_items(row, cases_by_id)
    if not gold_items:
        return None
    blob = predicted_evidence_blob(row)
    hits = sum(1 for item in gold_items if _item_in_blob(item, blob))
    return hits / len(gold_items)


def _skip_for_hops(row: dict[str, Any], cases_by_id: dict[str, dict], *, min_hops: int) -> bool:
    cid = str(row.get("case_id") or row.get("id") or "")
    case = cases_by_id.get(cid, {})
    hops = int(case.get("hops") or row.get("hop_count") or row.get("hops") or 0)
    return bool(hops and hops < min_hops)


# Inverse / alias relation types count as the same evidence for recall.
_RELATION_ALIASES: dict[str, frozenset[str]] = {
    "parent_of": frozenset({"parent_of", "subsidiary_of", "owns"}),
    "subsidiary_of": frozenset({"subsidiary_of", "parent_of", "owns"}),
    "works_at": frozenset({"works_at", "worked_at", "employed_by"}),
    "worked_at": frozenset({"worked_at", "works_at", "employed_by"}),
    "employed_by": frozenset({"employed_by", "worked_at", "works_at"}),
    "supplies": frozenset({"supplies", "supplies_for"}),
    "supplies_for": frozenset({"supplies_for", "supplies"}),
}


def _item_in_blob(item: str, blob: str) -> bool:
    """Require all meaningful segments of a gold item to appear (not any-one-hit)."""
    token = str(item).lower().strip()
    if not token:
        return False
    if _alias_hit(token, blob) or token in blob:
        return True
    segments = [s for s in token.replace("/", " ").split() if len(s) > 1]
    return bool(segments) and all(seg in blob for seg in segments)


def _alias_hit(token: str, blob: str) -> bool:
    aliases = _RELATION_ALIASES.get(token)
    return bool(aliases) and any(a in blob for a in aliases)


def fabrication_rate(rows: list[dict[str, Any]]) -> float:
    """Share of rows with answered status but no cited claims (AC-7 proxy)."""
    if not rows:
        return 0.0
    bad = 0
    counted = 0
    for row in rows:
        flag = _fabrication_flag(row)
        if flag is None:
            continue
        counted += 1
        if flag:
            bad += 1
    return (bad / counted) if counted else 0.0


def _fabrication_flag(row: dict[str, Any]) -> bool | None:
    """True=fabricated, False=ok, None=skip row."""
    status = str(row.get("status") or "").lower()
    if status in {"no_answer", ""}:
        return None
    chain = row.get("chain") or {}
    claims = chain.get("claims") if isinstance(chain, dict) else None
    if claims:
        return _claims_unbound(claims)
    if status == "answered" and not (row.get("prediction") or "").startswith("无法"):
        return True
    return False


def _claims_unbound(claims: list) -> bool:
    return any(not (c.get("evidence_ids") if isinstance(c, dict) else True) for c in claims)
