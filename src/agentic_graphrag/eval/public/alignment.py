"""Map adapter evidence back to canonical passage IDs (B-04).

Order: deterministic id → citation chunk → graph provenance → exact normalized text;
only then an audited fuzzy token-containment fallback. Every reference yields one
audit row, so coverage, missing and ambiguous mappings are reported, never hidden.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.eval.public.records import Passage, normalize_text, text_key

FUZZY_THRESHOLD = 0.8
FUZZY_MARGIN = 0.05
FUZZY_CANDIDATES = 5
MIN_FUZZY_TOKENS = 4
CHUNK_PREFIX = "chunk:"
NEIGHBOR_PREFIX = "nbr:"


class AlignMethod(StrEnum):
    ID = "id"
    CITATION = "citation"
    GRAPH = "graph_provenance"
    TEXT_EXACT = "text_exact"
    FUZZY = "fuzzy"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref_id: str = ""
    content: str = ""
    chunk_ids: tuple[str, ...] = ()


class Alignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref_id: str
    status: str  # mapped | missing | ambiguous
    passage_ids: tuple[str, ...] = ()
    method: AlignMethod | None = None
    score: float | None = None


class AlignmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[Alignment, ...] = Field(default_factory=tuple)

    def count(self, status: str) -> int:
        return sum(1 for a in self.items if a.status == status)

    @property
    def coverage(self) -> float | None:
        return self.count("mapped") / len(self.items) if self.items else None

    def summary(self) -> dict[str, float | int | None]:
        fuzzy = sum(1 for a in self.items if a.method is AlignMethod.FUZZY)
        return {
            "total": len(self.items),
            "mapped": self.count("mapped"),
            "missing": self.count("missing"),
            "ambiguous": self.count("ambiguous"),
            "fuzzy": fuzzy,
            "coverage": self.coverage,
        }


def relation_key(head: str, relation: str, tail: str) -> str:
    return f"{normalize_text(head)}|{relation}|{normalize_text(tail)}"


class PassageResolver:
    def __init__(
        self,
        passages: Iterable[Passage],
        *,
        chunk_to_passage: Mapping[str, str] | None = None,
        relation_sources: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.passages = {p.passage_id: p for p in passages}
        self.chunk_to_passage = dict(chunk_to_passage or {})
        self.relation_sources = dict(relation_sources or {})
        self.relation_types = {k.split("|")[1] for k in self.relation_sources}
        self._by_text: dict[str, list[str]] = defaultdict(list)
        self._tokens: dict[str, set[str]] = defaultdict(set)
        for pid, p in self.passages.items():
            self._by_text[text_key(p.text)].append(pid)
            self._by_text[text_key(p.index_text())].append(pid)
            for tok in set(normalize_text(p.index_text()).split()):
                self._tokens[tok].add(pid)

    def resolve(self, ref: EvidenceRef) -> Alignment:
        for method, ids in (
            (AlignMethod.ID, self._by_id(ref.ref_id)),
            (AlignMethod.CITATION, self._by_chunks(ref.chunk_ids)),
            (AlignMethod.GRAPH, self._by_graph(ref.ref_id)),
            (AlignMethod.TEXT_EXACT, self._by_exact_text(ref.content)),
        ):
            if ids:
                return _mapped(ref, ids, method)
        return self._fuzzy(ref)

    def _by_id(self, ref_id: str) -> tuple[str, ...]:
        rid = ref_id.removeprefix(CHUNK_PREFIX)
        if rid in self.passages:
            return (rid,)
        mapped = self.chunk_to_passage.get(rid)
        return (mapped,) if mapped else ()

    def _by_chunks(self, chunk_ids: tuple[str, ...]) -> tuple[str, ...]:
        found = [p for c in chunk_ids for p in self._by_id(c)]
        return tuple(dict.fromkeys(found))

    def _by_graph(self, ref_id: str) -> tuple[str, ...]:
        if not ref_id.startswith(NEIGHBOR_PREFIX):
            return ()
        body = ref_id.removeprefix(NEIGHBOR_PREFIX).rsplit(":", 1)[0]
        for rel in self.relation_types:
            head, sep, tail = body.partition(f":{rel}:")
            if sep:
                fwd = self.relation_sources.get(relation_key(head, rel, tail), ())
                rev = self.relation_sources.get(relation_key(tail, rel, head), ())
                return tuple(dict.fromkeys((*fwd, *rev)))
        return ()

    def _by_exact_text(self, content: str) -> tuple[str, ...]:
        if not content.strip():
            return ()
        return tuple(dict.fromkeys(self._by_text.get(text_key(content), [])))

    def _fuzzy(self, ref: EvidenceRef) -> Alignment:
        tokens = set(normalize_text(ref.content).split())
        if len(tokens) < MIN_FUZZY_TOKENS:
            return Alignment(ref_id=ref.ref_id, status="missing")
        votes: Counter[str] = Counter(pid for tok in tokens for pid in self._tokens.get(tok, ()))
        scored = [(n / len(tokens), pid) for pid, n in votes.most_common(FUZZY_CANDIDATES)]
        above = [(s, pid) for s, pid in scored if s >= FUZZY_THRESHOLD]
        if not above:
            return Alignment(ref_id=ref.ref_id, status="missing")
        best = above[0][0]
        tied = tuple(pid for s, pid in above if best - s < FUZZY_MARGIN)
        status = "mapped" if len(tied) == 1 else "ambiguous"
        return Alignment(
            ref_id=ref.ref_id,
            status=status,
            passage_ids=tied,
            method=AlignMethod.FUZZY,
            score=round(best, 4),
        )


def _mapped(ref: EvidenceRef, ids: tuple[str, ...], method: AlignMethod) -> Alignment:
    return Alignment(ref_id=ref.ref_id, status="mapped", passage_ids=ids, method=method)


def align(
    resolver: PassageResolver, refs: Iterable[EvidenceRef]
) -> tuple[tuple[str, ...], AlignmentReport]:
    """Ranked unique passage ids (mapped only) plus the per-reference audit report."""
    items = tuple(resolver.resolve(r) for r in refs)
    ranked = [pid for a in items if a.status == "mapped" for pid in a.passage_ids]
    return tuple(dict.fromkeys(ranked)), AlignmentReport(items=items)
