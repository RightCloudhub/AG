"""No-answer (phantom entity) gold case templates."""

from __future__ import annotations

from agentic_graphrag.eval.cases import CaseCategory, EvalCase
from agentic_graphrag.eval.gold_templates.context import EmitContext
from agentic_graphrag.eval.gold_templates.helpers import NO_ANSWER_TEXT, known_entities

HOPS_NONE = 0
_ID_PREFIX = "gen-noans"
_TEMPLATE_NAME = "no_answer_phantom"
_NOTES = "entity absent from pilot graph"

PHANTOM_ENTITIES: tuple[str, ...] = (
    "Zephyr Dynamics LLC",
    "Nimbus Quantum Corp",
    "Aurora Lattice Inc",
    "Phantom Holdings Group",
    "Void Systems AG",
    "Mirage Capital Partners",
    "Echo Robotics GmbH",
    "Nullspace Energy",
    "Chimera BioTech",
    "Specter Logistics Co",
    "Umbra Semiconductor Corp",
    "Fata Morgana AI",
    "Wraith Ventures",
    "Shade Industrial",
    "Gossamer Cloud Ltd",
    "Twilight Aggregates",
    "Hollow Point Materials",
    "Driftwood Mining",
    "Paper Tiger Motors",
    "Invisible Ink Media",
    "Moonbeam Cement",
    "Sandcastle Defense",
    "Quicksilver Rail Co",
    "Neverland Analytics",
    "Atlantis Robotics",
    "Shangri-La Semiconductors",
    "El Dorado Cloud",
    "Camelot Logistics",
    "Brigadoon Bio",
    "Ruritania Motors",
    "Narnia Energy",
    "Westeros Materials",
    "Gotham FinTech",
    "Metropolis Optics",
    "Wakanda Storage",
    "Asgard Networks",
)

NO_ANSWER_QUESTION_TEMPLATES: tuple[str, ...] = (
    "Who is the CEO of the parent company of {name}?",
    "Which companies did the CEO of {name} previously work at?",
    "What products does {name} produce?",
    "Who supplies {name}?",
)


def emit_no_answer_cases(ctx: EmitContext) -> int:
    """Emit phantom no-answer cases into ``ctx``."""
    known = known_entities(ctx.edges)
    n_templates = len(NO_ANSWER_QUESTION_TEMPLATES)
    for i, name in enumerate(PHANTOM_ENTITIES):
        if ctx.full():
            break
        if name in known:
            continue
        q = NO_ANSWER_QUESTION_TEMPLATES[i % n_templates].format(name=name)
        case = EvalCase(
            id=ctx.case_id(_ID_PREFIX),
            question=q,
            gold_answer=NO_ANSWER_TEXT,
            hops=HOPS_NONE,
            category=CaseCategory.NO_ANSWER,
            gold_path=[],
            gold_evidence=[],
            notes=_NOTES,
            metadata={"template": _TEMPLATE_NAME, "phantom": name},
        )
        ctx.try_add(case)
    return ctx.count
