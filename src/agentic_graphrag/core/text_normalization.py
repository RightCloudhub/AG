"""Lossless case-independent matching support, without stripping name punctuation."""

import re
import unicodedata

_PUNCTUATION = str.maketrans(
    {"’": "'", "‘": "'", "“": '"', "”": '"', "。": ".", "–": "-", "—": "-"}
)


def normalize_text(text: str) -> str:
    """Fold width/compatibility characters, typographic punctuation and whitespace."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).translate(_PUNCTUATION)).strip()
