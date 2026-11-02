"""Local-only tiktoken encoders and Unicode-safe token windows."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tiktoken
from pydantic import BaseModel

from agentic_graphrag.config import resolve_path
from agentic_graphrag.config_knowledge import DEFAULT_TOKENIZER

_VOCAB_HASH = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
# Matches tiktoken's cl100k_base pre-tokenizer (MIT; see tokenizers/LICENSE).
_CL100K_PATTERN = (
    r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+|"
    r" ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"
)


class EncodingSpec(BaseModel):
    name: str
    pat_str: str
    ranks_path: str


@dataclass(frozen=True)
class TokenCodec:
    encoder: tiktoken.Encoding

    @property
    def name(self) -> str:
        return self.encoder.name

    def count(self, text: str) -> int:
        return len(self.encoder.encode_ordinary(text))

    def prefix(self, text: str, limit: int) -> str:
        tokens = self.encoder.encode_ordinary(text)
        piece = self.encoder.decode(tokens[: max(0, limit)], errors="ignore")
        # Partial UTF-8 tokens are left in the caller's remainder, never replaced.
        while piece and self.count(piece) > limit:
            piece = piece[:-1]
        return piece

    def suffix(self, text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        tokens = self.encoder.encode_ordinary(text)
        piece = self.encoder.decode(tokens[-limit:], errors="ignore")
        while piece and self.count(piece) > limit:
            piece = piece[1:]
        return piece


@lru_cache
def get_tokenizer(tokenizer: str = DEFAULT_TOKENIZER) -> TokenCodec:
    """Load the bundled default or a local encoder JSON; never fetch URLs."""
    if tokenizer == DEFAULT_TOKENIZER:
        ranks = Path(__file__).with_name("tokenizers") / "cl100k_base.tiktoken"
        payload = ranks.read_bytes()
        if hashlib.sha256(payload).hexdigest() != _VOCAB_HASH:
            raise ValueError("Bundled tokenizer vocabulary checksum mismatch")
        pattern, name = _CL100K_PATTERN, DEFAULT_TOKENIZER
    else:
        path = resolve_path(tokenizer)
        spec = EncodingSpec.model_validate_json(path.read_text(encoding="utf-8"))
        payload = (path.parent / spec.ranks_path).read_bytes()
        pattern, name = spec.pat_str, spec.name
    vocabulary = {
        base64.b64decode(token): int(rank)
        for token, rank in (line.split() for line in payload.splitlines())
    }
    encoder = tiktoken.Encoding(
        name=name, pat_str=pattern, mergeable_ranks=vocabulary, special_tokens={}
    )
    return TokenCodec(encoder)
