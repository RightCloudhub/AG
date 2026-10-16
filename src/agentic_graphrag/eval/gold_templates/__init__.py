"""Path templates that emit multi-hop gold EvalCases from an edge index."""

from __future__ import annotations

from agentic_graphrag.eval.gold_templates.context import EmitContext
from agentic_graphrag.eval.gold_templates.guardrail import emit_guardrail_cases
from agentic_graphrag.eval.gold_templates.hop2 import emit_2hop_cases
from agentic_graphrag.eval.gold_templates.hop3 import emit_3hop_cases
from agentic_graphrag.eval.gold_templates.no_answer import emit_no_answer_cases
from agentic_graphrag.eval.gold_templates.open_cases import emit_open_cases

__all__ = [
    "EmitContext",
    "emit_2hop_cases",
    "emit_3hop_cases",
    "emit_guardrail_cases",
    "emit_no_answer_cases",
    "emit_open_cases",
]
