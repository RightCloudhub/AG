"""Entity resolution / disambiguation (FR-KG-04 / P3-KG-02).

Three-tier strategy:
1. Rule normalize (case, whitespace, alias dict)
2. Candidate generation (string similarity + optional embedding)
3. LLM merge judgment; uncertain → human review queue
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from agentic_graphrag.llm.provider import LLMProvider, Message, Tier
from agentic_graphrag.llm.structured import complete_structured
from agentic_graphrag.stores.interfaces import EntityRecord, GraphStore


def normalize_name(name: str) -> str:
    """Rule-level name normalization."""
    s = unicodedata.normalize("NFKC", name or "")
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    # Full/half width already via NFKC; fold case for key
    return s


def normalize_key(name: str) -> str:
    return normalize_name(name).lower()


def _token_set(name: str) -> set[str]:
    return {t for t in re.split(r"[^\w]+", normalize_key(name)) if t}


def jaccard(a: str, b: str) -> float:
    sa, sb = _token_set(a), _token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def char_ngram_sim(a: str, b: str, n: int = 3) -> float:
    def grams(s: str) -> set[str]:
        s = normalize_key(s)
        if len(s) < n:
            return {s}
        return {s[i : i + n] for i in range(len(s) - n + 1)}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


class MergeDecision(BaseModel):
    action: str = Field(description="merge | keep_separate | uncertain")
    confidence: float = 0.5
    rationale: str = ""
    canonical_name: str = ""


@dataclass
class ResolutionCandidate:
    left: EntityRecord
    right: EntityRecord
    score: float
    method: str = "similarity"


@dataclass
class ResolutionResult:
    merges: list[tuple[str, str, str]] = field(default_factory=list)  # (from_id, to_id, name)
    uncertain: list[ResolutionCandidate] = field(default_factory=list)
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "merges": [
                {"from_id": a, "to_id": b, "canonical": n} for a, b, n in self.merges
            ],
            "uncertain": [
                {
                    "left_id": c.left.id,
                    "right_id": c.right.id,
                    "left_name": c.left.name,
                    "right_name": c.right.name,
                    "score": c.score,
                }
                for c in self.uncertain
            ],
            "skipped": self.skipped,
        }


class EntityResolver:
    """Resolve duplicate entities in a GraphStore."""

    def __init__(
        self,
        *,
        alias_map: dict[str, str] | None = None,
        similarity_threshold: float = 0.72,
        auto_merge_threshold: float = 0.92,
        llm: LLMProvider | None = None,
    ) -> None:
        self.alias_map = {normalize_key(k): v for k, v in (alias_map or {}).items()}
        self.similarity_threshold = similarity_threshold
        self.auto_merge_threshold = auto_merge_threshold
        self.llm = llm

    def canonical_name(self, name: str) -> str:
        key = normalize_key(name)
        if key in self.alias_map:
            return self.alias_map[key]
        return normalize_name(name)

    def find_candidates(
        self,
        entities: list[EntityRecord],
        *,
        same_type_only: bool = True,
    ) -> list[ResolutionCandidate]:
        """Generate candidate merge pairs by string similarity."""
        by_type: dict[str, list[EntityRecord]] = {}
        for e in entities:
            by_type.setdefault(e.type if same_type_only else "*", []).append(e)

        pairs: list[ResolutionCandidate] = []
        for group in by_type.values():
            n = len(group)
            for i in range(n):
                for j in range(i + 1, n):
                    a, b = group[i], group[j]
                    if normalize_key(a.name) == normalize_key(b.name):
                        score = 1.0
                        method = "exact_norm"
                    else:
                        score = max(
                            jaccard(a.name, b.name),
                            char_ngram_sim(a.name, b.name),
                        )
                        # Alias map boost
                        if self.canonical_name(a.name) == self.canonical_name(b.name):
                            score = max(score, 0.95)
                            method = "alias"
                        else:
                            method = "similarity"
                    if score >= self.similarity_threshold:
                        pairs.append(
                            ResolutionCandidate(left=a, right=b, score=score, method=method)
                        )
        pairs.sort(key=lambda c: -c.score)
        return pairs

    def resolve(
        self,
        store: GraphStore,
        entities: list[EntityRecord] | None = None,
        *,
        allow_llm: bool = True,
        dry_run: bool = True,
    ) -> ResolutionResult:
        """Run resolution; optionally apply merges (when store supports alias update)."""
        if entities is None:
            # GraphStore protocol has no list_entities — caller should pass
            entities = []
            counts = store.counts()
            if counts.get("entities", 0) == 0:
                return ResolutionResult()

        candidates = self.find_candidates(entities)
        result = ResolutionResult()
        for cand in candidates:
            if cand.score >= self.auto_merge_threshold:
                canonical = self.canonical_name(cand.left.name)
                # Prefer longer / title-cased name as canonical surface
                if len(cand.right.name) > len(canonical):
                    canonical = normalize_name(cand.right.name)
                result.merges.append((cand.right.id, cand.left.id, canonical))
                if not dry_run:
                    self._apply_merge(store, cand.left, cand.right, canonical)
            elif allow_llm and self.llm is not None:
                decision = self._llm_decide(cand)
                if decision.action == "merge":
                    name = decision.canonical_name or cand.left.name
                    result.merges.append((cand.right.id, cand.left.id, name))
                    if not dry_run:
                        self._apply_merge(store, cand.left, cand.right, name)
                elif decision.action == "uncertain":
                    result.uncertain.append(cand)
                else:
                    result.skipped += 1
            else:
                result.uncertain.append(cand)
        return result

    def _llm_decide(self, cand: ResolutionCandidate) -> MergeDecision:
        try:
            return complete_structured(
                self.llm,  # type: ignore[arg-type]
                [
                    Message(
                        role="system",
                        content=(
                            "Decide if two entity mentions refer to the same real-world "
                            "entity. action=merge|keep_separate|uncertain."
                        ),
                    ),
                    Message(
                        role="user",
                        content=(
                            f"A: {cand.left.name} ({cand.left.type})\n"
                            f"B: {cand.right.name} ({cand.right.type})\n"
                            f"similarity={cand.score:.2f}"
                        ),
                    ),
                ],
                MergeDecision,
                tier=Tier.LIGHT,
            )
        except Exception:
            return MergeDecision(action="uncertain", confidence=0.0, rationale="llm failed")

    def _apply_merge(
        self,
        store: GraphStore,
        keep: EntityRecord,
        drop: EntityRecord,
        canonical: str,
    ) -> None:
        """Best-effort: upsert keep with alias of drop. Full edge rewiring is store-specific."""
        aliases = list(dict.fromkeys([*(keep.aliases or []), drop.name, drop.id]))
        keep.name = canonical
        keep.aliases = aliases
        store.upsert_entities([keep])
