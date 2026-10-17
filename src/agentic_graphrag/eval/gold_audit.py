"""Deterministic gold-label audit against the seed triples (ARCH_OPTIMIZATION_V2 O-2).

For each case with a ``gold_path`` the audit walks the relation chain from the
anchor entity over the triples and reports:

* ``path_edge_missing`` — a gold edge is absent in both directions;
* ``inverse_label``    — the edge is traversed backwards although the relation
  has a declared inverse (write SUBSIDIARY_OF, not reversed PARENT_OF).
  Backwards traversal of a relation *without* an inverse (``Company CEO_OF
  Person`` read as "CEO of") is the documented gold_path convention and is
  not flagged;
* ``gold_unreachable`` — a gold answer is not in the reachable answer set;
* ``ambiguous_gold``   — the chain reaches more answers than the gold lists.

Inverse pairs (PARENT_OF ↔ SUBSIDIARY_OF) and symmetric relations
(COMPETES_WITH, PARTNER_OF) are materialised before walking. The audit never
edits datasets; it only reports.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

INVERSE = {"PARENT_OF": "SUBSIDIARY_OF", "SUBSIDIARY_OF": "PARENT_OF"}
SYMMETRIC = frozenset({"COMPETES_WITH", "PARTNER_OF"})
_SPLIT = re.compile(r"\s*(?:,|;|/|\band\b)\s*", re.IGNORECASE)


def _name(node: Any) -> str:
    return str(node.get("name") if isinstance(node, dict) else node or "").strip()


@dataclass
class TripleIndex:
    """(head, REL) → tails, with inverse / symmetric edges materialised."""

    out: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, head: str, rel: str, tail: str) -> None:
        rel = rel.upper()
        self.out[(head.lower(), rel)].add(tail)
        if rel in SYMMETRIC:
            self.out[(tail.lower(), rel)].add(head)
        if rel in INVERSE:
            self.out[(tail.lower(), INVERSE[rel])].add(head)

    def has(self, head: str, rel: str, tail: str) -> bool:
        return tail.lower() in {t.lower() for t in self.out.get((head.lower(), rel), ())}

    def tails(self, head: str, rel: str) -> set[str]:
        return set(self.out.get((head.lower(), rel), ()))

    def heads(self, rel: str, tail: str) -> set[str]:
        tl = tail.lower()
        return {h for (h, r), ts in self.out.items() if r == rel and tl in _lower(ts)}


def _lower(names: set[str]) -> set[str]:
    return {n.lower() for n in names}


def load_triples(path: str | Path) -> TripleIndex:
    index = TripleIndex()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        index.add(_name(row.get("head")), str(row.get("relation") or ""), _name(row.get("tail")))
    return index


def gold_answers(gold: str) -> set[str]:
    return {p.strip().lower() for p in _SPLIT.split(gold or "") if p.strip()}


@dataclass
class CaseAudit:
    case_id: str
    issues: list[str] = field(default_factory=list)
    reachable: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "issues": self.issues, "reachable": self.reachable}


def _step(index: TripleIndex, frontier: set[str], step: tuple[str, str, str]) -> set[str]:
    src, rel, dst = step
    if _forward(index, step):
        return {t for f in frontier for t in index.tails(f, rel)}
    return {h for f in frontier for h in index.heads(rel, f)}


def _forward(index: TripleIndex, step: tuple[str, str, str]) -> bool:
    src, rel, dst = step
    return any(index.has(src, rel, d) for d in _names(dst))


def _names(node: str) -> list[str]:
    return [p for p in _SPLIT.split(node or "") if p.strip()] or [node]


def _resolve_rel(index: TripleIndex, hop: list[str]) -> tuple[str, str, str]:
    """Pick the matching label from hand-written alternatives ("SUBSIDIARY_OF/PARENT_OF")."""
    src, rel, dst = hop[0], str(hop[1]).upper(), hop[2]
    options = [r.strip() for r in rel.split("/") if r.strip()] or [rel]
    for option in options:
        if _forward(index, (src, option, dst)):
            return src, option, dst
    return src, options[0], dst


def _edge_issue(index: TripleIndex, step: tuple[str, str, str]) -> str | None:
    src, rel, dst = step
    if _forward(index, step):
        return None
    if any(index.has(d, rel, src) for d in _names(dst)):
        return "inverse_label" if rel in INVERSE else None
    return "path_edge_missing"


def audit_case(case: dict[str, Any], index: TripleIndex) -> CaseAudit:
    result = CaseAudit(case_id=str(case.get("id")))
    path = list(case.get("gold_path") or [])
    if len(path) < 3:
        return result
    steps = [_resolve_rel(index, path[i : i + 3]) for i in range(0, len(path) - 2, 2)]
    frontier = {path[0]}
    for step in steps:
        issue = _edge_issue(index, step)
        if issue:
            result.issues.append(f"{issue}:{'|'.join(step)}")
        frontier = _step(index, frontier, step)
    result.reachable = sorted(frontier)
    if str(case.get("category") or "") == "open":
        # Path-explanation golds ("A COMPETES_WITH B") name both ends: no answer set.
        return result
    _check_answers(result, gold_answers(str(case.get("gold_answer") or "")), _lower(frontier))
    return result


def _check_answers(result: CaseAudit, gold: set[str], reachable: set[str]) -> None:
    if gold and not gold <= reachable:
        result.issues.append("gold_unreachable")
    if len(reachable) > len(gold):
        result.issues.append("ambiguous_gold")


def audit_cases(cases: list[dict[str, Any]], index: TripleIndex) -> dict[str, Any]:
    audits = [audit_case(c, index) for c in cases]
    counts: Counter[str] = Counter()
    for a in audits:
        counts.update({issue.split(":", 1)[0] for issue in a.issues})
    flagged = [a.to_dict() for a in audits if a.issues]
    return {
        "total": len(audits),
        "flagged": len(flagged),
        "by_issue": dict(counts),
        "cases": flagged,
    }
