"""Query memory: evidence, paths, exclusions, sub-question state (FR-AG-05 / P2-AG-03).

Semantic logic (dedupe, excluded hypotheses, path loop prevention) is self-owned.
State payload is a plain typed dict suitable for LangGraph ``AgentState`` /
checkpointer serialization — framework stores bytes, this module judges meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypedDict

from agentic_graphrag.retrieval.contracts import Candidate


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", "", text)
    return text


def _near_duplicate_text(a: str, b: str) -> bool:
    """True only for exact or near-equal strings (small length delta).

    Prevents "Who is the CEO" from matching "Who is the CEO of Apex Holdings?"
    via loose substring rules while still catching trivial rephrases.
    """
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 12:
        return False
    if shorter not in longer:
        return False
    # Allow only tiny extensions (punctuation noise already stripped by normalize).
    return (len(longer) - len(shorter)) <= max(4, len(shorter) // 10)


class MemorySnapshot(TypedDict, total=False):
    """Serializable memory view for LangGraph typed state / checkpointer."""

    evidence: list[dict[str, Any]]
    explored_paths: list[str]
    explored_subquestions: list[str]
    excluded_hypotheses: list[str]
    conclusions: list[str]
    conclusions_by_subquestion: dict[str, str]
    done_subquestion_ids: list[str]


@dataclass
class MemoryState:
    """In-process memory with cross-sub-question evidence sharing."""

    evidence: dict[str, Candidate] = field(default_factory=dict)
    explored_paths: set[str] = field(default_factory=set)
    explored_subquestions: set[str] = field(default_factory=set)
    excluded_hypotheses: set[str] = field(default_factory=set)
    conclusions: list[str] = field(default_factory=list)
    conclusions_by_subquestion: dict[str, str] = field(default_factory=dict)
    done_subquestion_ids: set[str] = field(default_factory=set)

    def add_evidence(self, candidates: list[Candidate]) -> list[str]:
        """Merge candidates into shared evidence pool; return newly added ids."""
        added: list[str] = []
        for c in candidates:
            if c.id not in self.evidence:
                self.evidence[c.id] = c
                added.append(c.id)
            if c.is_graph():
                path_key = _normalize(c.content)
                self.explored_paths.add(path_key)
        return added

    def is_duplicate_subquestion(self, text: str) -> bool:
        key = _normalize(text)
        if not key:
            return True
        if key in self.explored_subquestions:
            return True
        for seen in self.explored_subquestions:
            if _near_duplicate_text(key, seen):
                return True
        return False

    def mark_subquestion(self, text: str) -> None:
        self.explored_subquestions.add(_normalize(text))

    def mark_subquestion_done(self, sq_id: str, conclusion: str | None = None) -> None:
        self.done_subquestion_ids.add(sq_id)
        if conclusion:
            self.conclusions_by_subquestion[sq_id] = conclusion
            self.conclusions.append(conclusion)

    def exclude_hypothesis(self, text: str) -> None:
        key = _normalize(text)
        if key:
            self.excluded_hypotheses.add(key)

    def is_excluded(self, text: str) -> bool:
        key = _normalize(text)
        if not key:
            return False
        if key in self.excluded_hypotheses:
            return True
        for ex in self.excluded_hypotheses:
            if _near_duplicate_text(key, ex):
                return True
        return False

    def is_path_explored(self, path_text: str) -> bool:
        return _normalize(path_text) in self.explored_paths

    def summary(self, max_items: int = 10) -> str:
        lines = [
            f"Evidence count: {len(self.evidence)}",
            f"Explored sub-questions: {len(self.explored_subquestions)}",
            f"Explored paths: {len(self.explored_paths)}",
            f"Excluded hypotheses: {len(self.excluded_hypotheses)}",
            f"Done sub-questions: {len(self.done_subquestion_ids)}",
        ]
        for _i, (eid, c) in enumerate(list(self.evidence.items())[:max_items]):
            lines.append(f"  - [{eid}] ({c.type}) {c.content[:120]}")
        if self.conclusions_by_subquestion:
            lines.append("Per-sub-question conclusions:")
            for sid, conc in list(self.conclusions_by_subquestion.items())[:max_items]:
                lines.append(f"  - {sid}: {conc[:120]}")
        elif self.conclusions:
            lines.append("Conclusions:")
            for c in self.conclusions[:max_items]:
                lines.append(f"  - {c}")
        if self.excluded_hypotheses:
            lines.append("Excluded:")
            for h in list(self.excluded_hypotheses)[:max_items]:
                lines.append(f"  - {h}")
        return "\n".join(lines)

    def evidence_list(self) -> list[Candidate]:
        """All shared evidence across sub-questions (FR-AG-05)."""
        return list(self.evidence.values())

    def to_snapshot(self) -> MemorySnapshot:
        """Export for LangGraph state / checkpointer (P2-AG-03)."""
        return MemorySnapshot(
            evidence=[c.model_dump(mode="json") for c in self.evidence.values()],
            explored_paths=sorted(self.explored_paths),
            explored_subquestions=sorted(self.explored_subquestions),
            excluded_hypotheses=sorted(self.excluded_hypotheses),
            conclusions=list(self.conclusions),
            conclusions_by_subquestion=dict(self.conclusions_by_subquestion),
            done_subquestion_ids=sorted(self.done_subquestion_ids),
        )

    @classmethod
    def from_snapshot(cls, snap: MemorySnapshot | dict[str, Any]) -> MemoryState:
        """Restore from LangGraph state / checkpointer payload."""
        mem = cls()
        _restore_evidence(mem, snap.get("evidence") or [])
        _restore_sets(mem, snap)
        return mem


def _restore_evidence(mem: MemoryState, raw_list: list[Any]) -> None:
    for raw in raw_list:
        c = Candidate.model_validate(raw)
        mem.evidence[c.id] = c
        if c.is_graph():
            mem.explored_paths.add(_normalize(c.content))


def _restore_sets(mem: MemoryState, snap: MemorySnapshot | dict[str, Any]) -> None:
    mem.explored_paths |= {_normalize(p) for p in (snap.get("explored_paths") or [])}
    mem.explored_subquestions = {
        _normalize(s) for s in (snap.get("explored_subquestions") or [])
    }
    mem.excluded_hypotheses = {_normalize(h) for h in (snap.get("excluded_hypotheses") or [])}
    mem.conclusions = list(snap.get("conclusions") or [])
    mem.conclusions_by_subquestion = dict(snap.get("conclusions_by_subquestion") or {})
    mem.done_subquestion_ids = set(snap.get("done_subquestion_ids") or [])
