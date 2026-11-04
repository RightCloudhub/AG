"""Generic corpus-summary intent, evaluated before short-question fact heuristics."""

import re

_SUMMARY = re.compile(
    r"概括|总结|综述|总体|整体|主要风险|共同风险|共性|主要主题|"
    r"\b(summari[sz]e|overview|overall|main risks|key risks|common themes|across (?:these|all))\b",
    re.IGNORECASE,
)


def is_global_question(question: str) -> bool:
    return bool(_SUMMARY.search(question))
