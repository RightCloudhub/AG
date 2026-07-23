"""Post-critic guards: force incomplete multi-hop chains to continue (badcase fix)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_graphrag.agent.critic import CriticResult
    from agentic_graphrag.agent.options import CritiqueContext

_NEEDS_PERSON_ROLE = re.compile(
    r"\b(who\s+is\s+the\s+)?(ceo|cfo|cto|president|founder)\b",
    re.I,
)
_NESTED_OF = re.compile(r"\bof\b.+\bof\b", re.I)
_CEO_EDGE = re.compile(r"\bCEO_OF\b|-[CEO_OF]->", re.I)
_ORG_EDGE = re.compile(
    r"\b(COMPETES_WITH|PRODUCES|PARENT_OF|SUBSIDIARY_OF|SUPPLIES)\b",
    re.I,
)
_IS_ORG = re.compile(
    r"\b(?:competitor|competitors|producer|parent(?:\s+company)?)\s+(?:of\s+.+?\s+)?is\s+([A-Z][\w\s&.-]{1,60})",
    re.I,
)
_ARE_ORGS = re.compile(
    r"\bcompetitors?\s+(?:of\s+.+?\s+)?are\s+([A-Z][\w\s&.,-]+)",
    re.I,
)
_EDGE_PAIR = re.compile(
    r"([A-Za-z0-9][\w\s&.-]{0,40}?)\s*-\[(?:COMPETES_WITH|PRODUCES|PARENT_OF)\]->\s*"
    r"([A-Za-z0-9][\w\s&.-]{0,40})",
)


def force_incomplete_multihop(
    ctx: CritiqueContext,
    result: CriticResult,
) -> CriticResult:
    """If Q asks for CEO-of-chain but evidence lacks CEO, force next_hop."""
    from agentic_graphrag.agent.critic import CriticAction, CriticScope

    if not _should_force(ctx, result):
        return result
    blob = _evidence_blob(ctx)
    target = _next_org_for_role(ctx, blob)
    if not target:
        return result
    role = _role_token(ctx.question or "") or "CEO"
    return result.model_copy(
        update={
            "action": CriticAction.NEXT_HOP,
            "scope": CriticScope.SUB_QUESTION,
            "rationale": (
                f"guard: original question needs {role} but evidence has no "
                f"{role}_OF edge; continue to {target}"
            ),
            "new_sub_question": f"Who is the {role} of {target}?",
            "global_answered": False,
            "sub_answered": True,
        }
    )


def _should_force(ctx: CritiqueContext, result: CriticResult) -> bool:
    from agentic_graphrag.agent.critic import CriticAction

    if result.action in (CriticAction.NEXT_HOP, CriticAction.REWRITE):
        return False
    if ctx.hop >= ctx.max_hops:
        return False
    # Respect remaining planned sub-questions (do not short-circuit 3-hop plans).
    if getattr(ctx, "remaining_subquestions", 0) > 0:
        return False
    q = ctx.question or ""
    if not _needs_role_person(q):
        return False
    blob = _evidence_blob(ctx)
    if _CEO_EDGE.search(blob) or _person_role_answered(blob):
        return False
    return bool(_ORG_EDGE.search(blob) or ctx.evidence)


def _needs_role_person(question: str) -> bool:
    if not _NEEDS_PERSON_ROLE.search(question or ""):
        return False
    if _NESTED_OF.search(question or ""):
        return True
    return bool(re.search(r"\b(competitor|producer|parent|subsidiary)\b", question or "", re.I))


def _role_token(question: str) -> str | None:
    m = re.search(r"\b(ceo|cfo|cto|president|founder)\b", question or "", re.I)
    return m.group(1).upper() if m else None


def _evidence_blob(ctx: CritiqueContext) -> str:
    parts = [str(ctx.sub_question or "")]
    for c in ctx.evidence or []:
        parts.append(getattr(c, "content", "") or "")
        parts.append(str(getattr(c, "id", "") or ""))
    for p in ctx.explored_paths or []:
        parts.append(str(p))
    return "\n".join(parts)


def _person_role_answered(blob: str) -> bool:
    return bool(_CEO_EDGE.search(blob))


def _next_org_for_role(ctx: CritiqueContext, blob: str) -> str | None:
    """Pick org from conclusions/evidence that still needs a role hop."""
    candidates = _orgs_from_evidence(ctx) + _orgs_from_text(ctx, blob)
    return _first_org_without_ceo(candidates, blob)


def _orgs_from_evidence(ctx: CritiqueContext) -> list[str]:
    out: list[str] = []
    for c in ctx.evidence or []:
        content = getattr(c, "content", "") or ""
        m = _EDGE_PAIR.search(content)
        if not m:
            continue
        left, right = m.group(1).strip(), m.group(2).strip()
        out.extend((right, left))
    return out


def _orgs_from_text(ctx: CritiqueContext, blob: str) -> list[str]:
    out: list[str] = []
    for src in (ctx.sub_question, blob):
        out.extend(_names_from_is_org(src or ""))
        out.extend(_names_from_are_orgs(src or ""))
    return out


def _names_from_is_org(text: str) -> list[str]:
    return [m.group(1).strip().rstrip(".") for m in _IS_ORG.finditer(text)]


def _names_from_are_orgs(text: str) -> list[str]:
    out: list[str] = []
    for m in _ARE_ORGS.finditer(text):
        for part in re.split(r",| and ", m.group(1)):
            name = part.strip().rstrip(".")
            if name:
                out.append(name)
    return out


def _first_org_without_ceo(candidates: list[str], blob: str) -> str | None:
    seen: set[str] = set()
    for name in candidates:
        key = name.lower()
        if key in seen or len(name) < 2:
            continue
        seen.add(key)
        pat = rf"{re.escape(name)}.{{0,40}}CEO_OF|CEO_OF.{{0,40}}{re.escape(name)}"
        if re.search(pat, blob):
            continue
        return name
    return None
