"""Lexical normalization shared by planner and triage; entity names are protected."""

import re
from functools import lru_cache

from agentic_graphrag.agent.entity_linking import entity_spans, repair_entity_mentions
from agentic_graphrag.agent.entity_mentions import lexicon_from_names
from agentic_graphrag.config_matching import question_matching_config
from agentic_graphrag.core.text_normalization import normalize_text


@lru_cache(maxsize=1)
def _aliases() -> tuple[re.Pattern[str], dict[str, str]]:
    aliases = {
        alias.casefold(): key
        for key, values in question_matching_config().aliases.items()
        for alias in values
    }
    body = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) or r"(?!)"
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.I), aliases


def normalize_question(question: str, known_entities: list[str] | None = None) -> str:
    names = tuple(lexicon_from_names(known_entities))
    text = repair_entity_mentions(normalize_text(question), names)
    occupied = entity_spans(text, names)
    pattern, aliases = _aliases()

    def replace(match: re.Match[str]) -> str:
        if any(match.start() < end and match.end() > start for start, end in occupied):
            return match.group()
        return aliases[match.group().casefold()]

    return pattern.sub(replace, text)
