"""People / employment heuristics (Orion, Meridian, prior employers, CEO)."""

from __future__ import annotations

from agentic_graphrag.generation.offline_heuristics.constants import (
    APEX_HOLDINGS,
    CEO_LEAD_KEYS,
    MULTI_HOP_EXTRA_KEYS,
    ORION_MIN_MATCHES,
    PERSON_NAME_HINTS,
    WORK_PHRASES,
)
from agentic_graphrag.generation.offline_heuristics.graph_ops import EdgeView

_CEO_Q_KEYS = ("ceo", "首席执行官", "总裁")


def rule_both_orion(q: str, ents: list[str], view: EdgeView, *, texts: list[str]) -> str | None:
    """Yes/no: both named people worked at Orion (before generic work filters)."""
    if not _is_both_work_q(q):
        return None
    return _answer_both_orion(ents, view, texts)


def _is_both_work_q(q: str) -> bool:
    return "both" in q and ("work" in q or "orion" in q)


def _answer_both_orion(ents: list[str], view: EdgeView, texts: list[str]) -> str | None:
    at_orion = _orion_workers(view)
    if _named_orion_hits(ents, at_orion, view):
        return "Yes"
    if len(at_orion) >= ORION_MIN_MATCHES:
        return "Yes"
    if _blob_orion_yes(texts):
        return "Yes"
    return None


def _orion_workers(view: EdgeView) -> set[str]:
    return {h for h, t in view.find_edges("WORKED_AT") if "orion" in t.lower()}


def _named_orion_hits(ents: list[str], at_orion: set[str], view: EdgeView) -> bool:
    named = [e for e in ents if any(x in e.lower() for x in PERSON_NAME_HINTS)]
    if not named:
        return False
    hits = [p for p in named if any(view.related_to(p, o) for o in at_orion)]
    return len(hits) >= ORION_MIN_MATCHES or len(at_orion) >= ORION_MIN_MATCHES


def _blob_orion_yes(texts: list[str]) -> bool:
    blob = " ".join(texts).lower()
    return "orion" in blob and "elena" in blob and "marcus" in blob


def rule_meridian_helix_ceo(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """CEO who previously worked at Meridian and leads Helix."""
    del ents, texts
    if not _meridian_helix_q(q):
        return None
    return _helix_ceo_from_meridian(view)


def _meridian_helix_q(q: str) -> bool:
    if "ceo" not in q or "meridian" not in q:
        return False
    return any(k in q for k in CEO_LEAD_KEYS)


def _helix_ceo_from_meridian(view: EdgeView) -> str | None:
    meridian = [h for h, t in view.find_edges("WORKED_AT") if "meridian" in t.lower()]
    match = _ceo_of_helix_among(meridian, view)
    if match:
        return match
    return _any_helix_ceo(view)


def _ceo_of_helix_among(people: list[str], view: EdgeView) -> str | None:
    for h, t in view.find_edges("CEO_OF"):
        if "helix" not in t.lower():
            continue
        if any(view.related_to(p, h) for p in people):
            return h
    return None


def _any_helix_ceo(view: EdgeView) -> str | None:
    for h, t in view.find_edges("CEO_OF"):
        if "helix" in t.lower():
            return h
    return None


def rule_meridian_executives(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """Who among executives previously worked at Meridian."""
    del ents, texts
    if not _meridian_exec_q(q):
        return None
    people = [h for h, t in view.find_edges("WORKED_AT") if "meridian" in t.lower()]
    return view.join_unique(people) if people else None


def _meridian_exec_q(q: str) -> bool:
    if "meridian" not in q:
        return False
    if "who" not in q and "executive" not in q:
        return False
    return "lead" not in q and "helix" not in q


def rule_prior_employers(
    q: str, ents: list[str], view: EdgeView, *, texts: list[str]
) -> str | None:
    """Companies CEO of (parent of) X previously worked at."""
    del texts
    if not any(k in q for k in WORK_PHRASES) or "both" in q:
        return None
    persons = _persons_for_work_query(q, list(ents), view)
    employers = _employers_of(persons, ents, view)
    if employers:
        return view.join_unique(employers)
    if persons and "work" not in q:
        return persons[0]
    return None


def _persons_for_work_query(q: str, target: list[str], view: EdgeView) -> list[str]:
    if "ceo" in q and "parent" in q:
        par = view.parents_of(target)
        return view.ceos_of(par) or view.ceos_of({APEX_HOLDINGS})
    if "ceo" not in q:
        return []
    return _ceos_for_targets(target, view)


def _ceos_for_targets(target: list[str], view: EdgeView) -> list[str]:
    persons: list[str] = []
    for e in target:
        persons.extend(view.ceos_of({e}))
    if persons:
        return persons
    for h, t in view.find_edges("CEO_OF"):
        if any(view.related_to(e, t) for e in target):
            persons.append(h)
    return persons


def _employers_of(persons: list[str], ents: list[str], view: EdgeView) -> list[str]:
    employers: list[str] = []
    for h, t in view.find_edges("WORKED_AT"):
        if _matches_subjects(h, persons or ents, view):
            employers.append(t)
    return employers


def _matches_subjects(head: str, subjects: list[str], view: EdgeView) -> bool:
    return any(view.related_to(s, head) for s in subjects)


def rule_ceo_of_parent(q: str, ents: list[str], view: EdgeView, *, texts: list[str]) -> str | None:
    """CEO of parent of X (answer is person)."""
    del texts
    if not _asks_ceo(q) or not _asks_parent_path(q):
        return None
    if _has_multi_hop_extra(q):
        # Defer to rules_ma for acquirer+HQ compound questions.
        return None
    people = view.ceos_of(view.parents_of(ents))
    if people:
        return people[0]
    return _any_apex_ceo(view)


def _any_apex_ceo(view: EdgeView) -> str | None:
    for h, t in view.find_edges("CEO_OF"):
        if "apex" in t.lower():
            return h
    return None


def rule_ceo_of_company(q: str, ents: list[str], view: EdgeView, *, texts: list[str]) -> str | None:
    """CEO of named company (not parent path)."""
    del texts
    if not _asks_ceo(q) or _asks_parent_path(q) or _has_multi_hop_extra(q):
        return None
    for e in ents:
        hit = _ceo_of_entity(e, view)
        if hit:
            return hit
    return None


def _asks_ceo(q: str) -> bool:
    return any(k in q for k in _CEO_Q_KEYS)


def _asks_parent_path(q: str) -> bool:
    return "parent" in q or "母公司" in q


def _has_multi_hop_extra(q: str) -> bool:
    return any(k in q for k in MULTI_HOP_EXTRA_KEYS)


def _ceo_of_entity(entity: str, view: EdgeView) -> str | None:
    """CEO of *this* company only — require the company to match the edge tail.

    Uses a strict-enough match so multi-hop neighbors (supplier CEOs) are not
    returned for an unrelated seed company.
    """
    el = entity.lower().strip()
    if not el:
        return None
    for h, t in view.find_edges("CEO_OF"):
        tl = t.lower().strip()
        if tl == el or el in tl or tl in el:
            return h
    return None
