"""Decode text without replacement characters or first-chunk BOM leakage."""

from __future__ import annotations

import codecs
import unicodedata
from dataclasses import dataclass

from charset_normalizer import from_bytes

from agentic_graphrag.config_knowledge import ParsingConfig
from agentic_graphrag.core.ports.parsing import UndecodableDocument


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str


def normalize_text(text: str) -> str:
    """Canonical UTF-8-serializable text; preserve paragraph and code whitespace."""
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def decode_text(content: bytes, config: ParsingConfig) -> DecodedText:
    try:
        text = content.decode("utf-8-sig")
        encoding = "utf-8-sig" if content.startswith(codecs.BOM_UTF8) else "utf-8"
    except UnicodeDecodeError:
        return _detect_text(content, config)
    return _validated(text, encoding)


def _detect_text(content: bytes, config: ParsingConfig) -> DecodedText:
    matches = from_bytes(
        content, threshold=1 - config.encoding_min_confidence, enable_fallback=False
    )
    best = matches.best()
    if best is None or 1 - best.chaos < config.encoding_min_confidence:
        raise UndecodableDocument("Cannot reliably detect text encoding; convert to UTF-8")
    preferred = next(
        (
            match
            for match in matches
            if codecs.lookup(match.encoding).name == config.encoding_preference
        ),
        None,
    )
    if preferred and preferred.chaos == best.chaos and preferred.coherence >= best.coherence:
        best = preferred
    try:
        text = content.decode(best.encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise UndecodableDocument("Detected encoding cannot decode the complete document") from exc
    return _validated(text, best.encoding)


def _validated(text: str, encoding: str) -> DecodedText:
    if any(unicodedata.category(char) == "Cc" and char not in "\t\r\n\f" for char in text):
        raise UndecodableDocument("Content contains binary control bytes; convert to UTF-8 text")
    return DecodedText(normalize_text(text), encoding)
