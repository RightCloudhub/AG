"""Conservative Chinese possessive chains over the existing relation frames."""

import re

from agentic_graphrag.agent.relation_frames import frame
from agentic_graphrag.config_matching import question_matching_config
from agentic_graphrag.core.text_normalization import normalize_text

_QUESTION_END = re.compile(r"(?:是)?(?:谁|哪位|哪家公司|什么)?[?!. ]*$")
_APPOINTED = re.compile(r"由谁(?:担任|出任)")


def chinese_chain_text(question: str, entities: list[str]) -> str | None:
    """Translate only fully consumed ``entity 的 relation 的 relation`` chains.

    No word-order guessing: relative clauses or unrecognized text fall through.
    Repeated frames remain repeated hops, including parent-of-parent chains.
    """
    text = normalize_text(question).casefold()
    cues = sorted(
        (
            (cue, key)
            for key, cues in question_matching_config().chinese_frames.items()
            for cue in cues
        ),
        reverse=True,
        key=lambda pair: len(pair[0]),
    )
    for entity in sorted(entities, key=len, reverse=True):
        anchor = normalize_text(entity).casefold()
        if not text.startswith(anchor):
            continue
        rest = _QUESTION_END.sub("", _APPOINTED.sub("", text[len(anchor) :])).strip()
        keys = _consume_cues(rest, cues)
        if keys:
            result = anchor
            for key in keys:
                result = f"{frame(key).nouns[0]} of {result}"
            return "what is the " + result
    return None


def _consume_cues(rest: str, cues: list[tuple[str, str]]) -> list[str]:
    keys = []
    while rest:
        rest = rest.removeprefix("的").strip()
        found = next(((cue, key) for cue, key in cues if rest.startswith(cue)), None)
        if found is None:
            return []
        cue, key = found
        keys.append(key)
        rest = rest[len(cue) :].strip()
    return keys
