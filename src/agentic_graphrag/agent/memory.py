"""Query memory: evidence, explored paths, sub-questions (FR-AG-05)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentic_graphrag.retrieval.contracts import Candidate


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s\u4e00-\u9fff]", "", text)
    return text


@dataclass
class MemoryState:
    evidence: dict[str, Candidate] = field(default_factory=dict)
    explored_paths: set[str] = field(default_factory=set)
    explored_subquestions: set[str] = field(default_factory=set)
    excluded_hypotheses: set[str] = field(default_factory=set)
    conclusions: list[str] = field(default_factory=list)

    def add_evidence(self, candidates: list[Candidate]) -> list[str]:
        added: list[str] = []
        for c in candidates:
            if c.id not in self.evidence:
                self.evidence[c.id] = c
                added.append(c.id)
            # Track graph paths for loop prevention
            if c.source.value == "graph":
                path_key = _normalize(c.content)
                self.explored_paths.add(path_key)
        return added

    def is_duplicate_subquestion(self, text: str) -> bool:
        key = _normalize(text)
        if not key:
            return True
        if key in self.explored_subquestions:
            return True
        # Near-duplicate: substring containment for short questions
        for seen in self.explored_subquestions:
            if key in seen or seen in key:
                if min(len(key), len(seen)) >= 8:
                    return True
        return False

    def mark_subquestion(self, text: str) -> None:
        self.explored_subquestions.add(_normalize(text))

    def is_path_explored(self, path_text: str) -> bool:
        return _normalize(path_text) in self.explored_paths

    def summary(self, max_items: int = 10) -> str:
        lines = [
            f"Evidence count: {len(self.evidence)}",
            f"Explored sub-questions: {len(self.explored_subquestions)}",
            f"Explored paths: {len(self.explored_paths)}",
        ]
        for _i, (eid, c) in enumerate(list(self.evidence.items())[:max_items]):
            lines.append(f"  - [{eid}] {c.content[:120]}")
        if self.conclusions:
            lines.append("Conclusions:")
            for c in self.conclusions[:max_items]:
                lines.append(f"  - {c}")
        return "\n".join(lines)

    def evidence_list(self) -> list[Candidate]:
        return list(self.evidence.values())
