"""Explicit planning failures for knowledge bases without a tested rule profile."""


class PlanningUnavailable(RuntimeError):
    """No valid LLM plan exists; domain heuristics must not be substituted."""
