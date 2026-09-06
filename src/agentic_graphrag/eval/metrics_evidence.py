"""Evidence-recall helpers for evaluation metrics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentic_graphrag.generation.claim_support import content_tokens


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
    """Share of answered rows whose claims fail the eval-side citation gate (AC-7).

    Aligned with the runtime gate in :mod:`agentic_graphrag.generation.citations`:
    every claim must carry evidence ids, those ids must resolve inside the row's
    own evidence catalog, and the claim text must share a content token with the
    cited content (CJK-aware, BL-02). The runtime *object anchor* tier (BL-13)
    cannot be reproduced here — the persisted catalog keeps only id/content and
    drops ``structured`` — so this remains an upper bound on grounding, not an
    entailment check. Rows whose only cited evidence was truncated on the way
    into the catalog are skipped rather than accused: the supporting token may
    sit past the cut, and being *stricter* than the runtime gate would be just
    as wrong as being looser.
    """
    return _rate(rows, _fabrication_flag)


def unbound_claim_rate(rows: list[dict[str, Any]]) -> float:
    """Share of answered rows carrying a claim with no evidence ids at all.

    This is the historical ``fabrication_rate`` criterion under a name that
    matches what it measures: the weakest of the three citation tiers, blind to
    a claim that cites a real id whose content asserts something else (BL-13).
    Kept alongside the strengthened metric so the historical series stays
    comparable across reports.
    """
    return _rate(rows, _unbound_flag)


@dataclass(frozen=True)
class _CatalogEntry:
    """Persisted evidence text, plus whether it was cut on the way in."""

    content: str
    truncated: bool = False


# id → persisted evidence; the callables that consume a row or its claims.
_Catalog = dict[str, _CatalogEntry]
_RowFlag = Callable[[dict[str, Any]], bool | None]
_ClaimJudge = Callable[[list, _Catalog], bool]


def _rate(rows: list[dict[str, Any]], flag_of: _RowFlag) -> float:
    bad = 0
    counted = 0
    for row in rows:
        flag = flag_of(row)
        if flag is None:
            continue
        counted += 1
        if flag:
            bad += 1
    return (bad / counted) if counted else 0.0


def _fabrication_flag(row: dict[str, Any]) -> bool | None:
    """True=fabricated, False=ok, None=skip row."""
    return _row_flag(row, _claims_fail_gate)


def _unbound_flag(row: dict[str, Any]) -> bool | None:
    return _row_flag(row, lambda claims, _catalog: _claims_unbound(claims))


def _row_flag(row: dict[str, Any], judge: _ClaimJudge) -> bool | None:
    status = str(row.get("status") or "").lower()
    if status in {"no_answer", ""}:
        return None
    chain = row.get("chain") or {}
    claims = chain.get("claims") if isinstance(chain, dict) else None
    if claims:
        return bool(judge(claims, _catalog_by_id(chain)))
    if status == "answered" and not (row.get("prediction") or "").startswith("无法"):
        return True
    return False


def _claims_fail_gate(claims: list, catalog: _Catalog) -> bool:
    if _claims_unbound(claims):
        return True
    if not catalog:
        # Older reports and baseline rows carry no evidence catalog; only the
        # id-presence tier is checkable, so do not accuse the row of fabricating.
        return False
    return any(not _claim_grounded(c, catalog) for c in claims if isinstance(c, dict))


def _claim_grounded(claim: dict[str, Any], catalog: _Catalog) -> bool:
    """One claim must cite a catalog id whose content shares a content token."""
    cited = _cited_entries(claim, catalog)
    if not cited:
        return False
    claim_tokens = content_tokens(str(claim.get("text") or ""))
    if not claim_tokens:
        return True  # nothing lexical to check — mirrors the runtime gate
    if any(claim_tokens & content_tokens(entry.content) for entry in cited):
        return True
    # Truncated content: the token that satisfied the runtime gate may sit past
    # the persisted cut, so abstain instead of over-reporting fabrication.
    return any(entry.truncated for entry in cited)


def _cited_entries(claim: dict[str, Any], catalog: _Catalog) -> list[_CatalogEntry]:
    ids = [str(i) for i in (claim.get("evidence_ids") or [])]
    return [catalog[i] for i in ids if i in catalog]


def _catalog_by_id(chain: dict[str, Any]) -> _Catalog:
    """Evidence id → persisted text from the catalog (absent in older rows)."""
    meta = chain.get("metadata") if isinstance(chain.get("metadata"), dict) else {}
    catalog = chain.get("evidence") or (meta.get("evidence") if meta else None) or []
    out: _Catalog = {}
    for ev in catalog:
        if isinstance(ev, dict) and ev.get("id"):
            out[str(ev["id"])] = _CatalogEntry(
                content=str(ev.get("content") or ""),
                truncated=bool(ev.get("truncated")),
            )
    return out


def _claims_unbound(claims: list) -> bool:
    return any(not (c.get("evidence_ids") if isinstance(c, dict) else True) for c in claims)
