"""Claim ↔ evidence lexical support (BL-02 / BL-13).

Two defects in the previous single-function implementation motivated this module:

* **BL-02 (false negative).** Tokenization split on whitespace only, so a
  pure-CJK claim collapsed into one token that no evidence could ever match and
  the gate rejected it unconditionally — burning two STRONG LLM calls before
  falling back to "cannot answer".
* **BL-13 (false positive).** One shared token was enough to pass, and that
  token was usually the *subject* entity. A claim could therefore cite a real,
  retrieved edge that asserts an entirely different relation and still pass.

Fixing only the first would worsen the second (finer tokens make a 1-token
overlap easier to reach), so both are handled here:

1. tokenization is CJK-aware — character **bigrams**, not unigrams, because
   single Han characters are far too common to be discriminative;
2. graph evidence must be anchored on what the edge actually asserts: a
   neighbor edge requires the claim to name the **far endpoint** — the end that
   is not the query entity — and a path requires the claim to name at least two
   of its nodes.

This is still not entailment. See ``docs/BUSINESS_LOGIC.md`` BL-13 for the
remaining gap and the cost note on adding a real NLI check.
"""

from __future__ import annotations

from typing import Any

MIN_LATIN_TOKEN_LEN = 2
CJK_BIGRAM_SIZE = 2
MIN_PATH_NODE_HITS = 2
DEFAULT_MIN_OVERLAP = 1

_STOPWORDS = frozenset(
    "a an the of to in on for and or is are was were be by with from as at".split()
)

# Han, kana and Hangul blocks — scripts written without word separators.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)

_KIND_CJK = "cjk"
_KIND_LATIN = "latin"
_KIND_SKIP = ""


def content_tokens(text: str) -> set[str]:
    """Script-aware content tokens.

    Latin/digit runs become whole tokens; CJK runs become character bigrams so
    that scripts without spaces still produce comparable units (BL-02).
    """
    tokens: set[str] = set()
    for kind, chunk in _segments((text or "").lower()):
        if kind == _KIND_CJK:
            tokens |= _cjk_tokens(chunk)
        elif len(chunk) >= MIN_LATIN_TOKEN_LEN and chunk not in _STOPWORDS:
            tokens.add(chunk)
    return tokens


def claim_supported_by(
    claim_tokens: set[str],
    candidate: Any,
    *,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
    require_object_anchor: bool = True,
) -> bool:
    """True when one cited candidate lexically supports the claim."""
    evidence_tokens = content_tokens(getattr(candidate, "content", "") or "")
    if len(claim_tokens & evidence_tokens) < min_overlap:
        return False
    if not require_object_anchor:
        return True
    return _object_anchored(claim_tokens, candidate)


def _object_anchored(claim_tokens: set[str], candidate: Any) -> bool:
    """Graph evidence must be anchored on the entity the edge asserts (BL-13).

    Without this, ``Apex Holdings -[FOUNDED_BY]-> X`` supports the claim
    "Apex Holdings' CEO is Y" purely through the shared subject.
    """
    structured = getattr(candidate, "structured", None) or {}
    if not isinstance(structured, dict):
        return True
    kind = str(structured.get("kind") or "")
    if kind == "neighbor":
        names = _neighbor_anchor_names(structured)
        return not names or _names_touched(claim_tokens, names) >= 1
    if kind == "path":
        nodes = [str(n) for n in (structured.get("nodes") or []) if str(n).strip()]
        if not nodes:
            return True
        return _names_touched(claim_tokens, nodes) >= min(MIN_PATH_NODE_HITS, len(nodes))
    return True


def _neighbor_anchor_names(structured: dict[str, Any]) -> list[Any]:
    """The endpoint a neighbor edge asserts, excluding the query entity.

    ``tail`` alone is not that endpoint: both graph backends traverse edges
    undirected, so an inbound edge (``Elena -[CEO_OF]-> Apex`` retrieved for
    "Apex") puts the *query* entity in ``tail`` and the anchor decays back into
    the subject-only match BL-13 removed. ``neighbor`` is the far end in every
    edge the retriever emits; ``tail``/``head`` are fallbacks for producers that
    omit it. Empty means "not anchorable" — the caller then lets the claim pass,
    as it does for evidence with no structured shape at all.
    """
    query = _normalized(structured.get("query_entity"))
    for key in ("neighbor", "tail", "head"):
        name = structured.get(key)
        if str(name or "").strip() and _normalized(name) != query:
            return [name]
    return []


def _normalized(name: Any) -> str:
    """Case- and whitespace-insensitive form for endpoint identity checks."""
    return " ".join(str(name or "").lower().split())


def _names_touched(claim_tokens: set[str], names: list[Any]) -> int:
    """How many of ``names`` share at least one token with the claim."""
    hits = 0
    for name in names:
        name_tokens = content_tokens(str(name or ""))
        if name_tokens and (name_tokens & claim_tokens):
            hits += 1
    return hits


def _cjk_tokens(chunk: str) -> set[str]:
    """Character bigrams for a CJK run (unigram only for 1-character runs)."""
    if len(chunk) < CJK_BIGRAM_SIZE:
        return {chunk}
    return {chunk[i : i + CJK_BIGRAM_SIZE] for i in range(len(chunk) - CJK_BIGRAM_SIZE + 1)}


def _segments(text: str) -> list[tuple[str, str]]:
    """Split into maximal same-script runs, dropping separators/punctuation."""
    out: list[tuple[str, str]] = []
    kind = _KIND_SKIP
    buf: list[str] = []
    for char in text:
        current = _char_kind(char)
        if current != kind:
            _flush(out, kind, buf)
            kind, buf = current, []
        if current != _KIND_SKIP:
            buf.append(char)
    _flush(out, kind, buf)
    return out


def _flush(out: list[tuple[str, str]], kind: str, buf: list[str]) -> None:
    if kind != _KIND_SKIP and buf:
        out.append((kind, "".join(buf)))


def _char_kind(char: str) -> str:
    if _is_cjk(char):
        return _KIND_CJK
    return _KIND_LATIN if char.isalnum() else _KIND_SKIP


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)
