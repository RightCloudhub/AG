"""Code-enforced register transitions. Model output is always a proposal."""

from __future__ import annotations

from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.core.evidence import Candidate


def validate_dependencies(register: Register) -> None:
    remaining = {rid: set(r.depends_on) for rid, r in register.items.items()}
    if any(deps - remaining.keys() for deps in remaining.values()):
        raise ValueError("unknown requirement dependency")
    while remaining:
        ready = {rid for rid, deps in remaining.items() if not deps}
        if not ready:
            raise ValueError("cyclic requirement dependencies")
        remaining = {rid: deps - ready for rid, deps in remaining.items() if rid not in ready}


def apply_invariants(
    new: Register,
    old: Register,
    *,
    evidence: dict[str, Candidate],
    fresh_ids: set[str] | None = None,
) -> Register:
    """Merge without mutating either input; reject fabricated passage references."""
    result = new.model_copy(deep=True)
    result.question = old.question
    for rid, previous in old.items.items():
        proposed = result.items.get(rid)
        if proposed is None:
            result.items[rid] = previous.model_copy(deep=True)
        else:
            result.items[rid] = _merge_item(
                proposed, previous, evidence=evidence, fresh_ids=fresh_ids
            )
    for rid, item in result.items.items():
        item.req_id = rid
        item.passages &= evidence.keys()
        _check_closure(item)
        _ground_anchors(item, evidence)
    validate_dependencies(result)
    schedule(result)
    return result


def _merge_item(
    proposed: Requirement,
    previous: Requirement,
    *,
    evidence: dict[str, Candidate],
    fresh_ids: set[str] | None,
) -> Requirement:
    touched = proposed.touched_passages & set(evidence)
    if fresh_ids is not None and not touched & fresh_ids:
        return previous.model_copy(deep=True)
    # Refinements belong in notes/anchors. Changing the need itself could erase
    # an unanswered part while retaining its id and marking an easier need done.
    proposed.text = previous.text
    proposed.depends_on = list(previous.depends_on)
    proposed.anchors = list(dict.fromkeys([*previous.anchors, *proposed.anchors]))
    proposed.passages = (proposed.passages & evidence.keys()) | previous.passages
    if proposed.status == "resolved" and not proposed.passages:
        proposed.status = previous.status
    if proposed.status == "retired" and not proposed.retire_reason.strip():
        proposed.status = previous.status
    return proposed


def _check_closure(item: Requirement) -> None:
    if item.status == "resolved" and not item.passages:
        item.status = "pending"
    if item.status == "retired" and not item.retire_reason.strip():
        item.status = "pending"


def _ground_anchors(item: Requirement, evidence: dict[str, Candidate]) -> None:
    ids = item.passages | item.touched_passages
    content = " ".join(evidence[i].content for i in ids if i in evidence).casefold()
    item.anchors = list(dict.fromkeys(a for a in item.anchors if a and a.casefold() in content))
    item.touched_passages &= evidence.keys()


def schedule(register: Register, *, attempts: dict[str, int] | None = None) -> None:
    """Exactly one active ready need; blocked dependencies remain visibly open."""
    ready = register.ready_items()
    active = [r for r in ready if r.status == "active"]
    selected = active[0] if len(active) == 1 else None
    if attempts is not None or selected is None:
        counts = attempts or {}
        selected = min(ready, key=lambda r: counts.get(r.req_id, 0), default=None)
    for item in register.open_items():
        item.status = "active" if item is selected else "pending"
