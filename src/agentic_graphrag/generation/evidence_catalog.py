"""Evidence catalog persisted on the chain so API/UI can resolve citation clicks."""

from __future__ import annotations

from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain

# Persisted-evidence caps: bound audit-row size without dropping citable rows.
EVIDENCE_CATALOG_LIMIT = 50
EVIDENCE_CONTENT_CHARS = 800


def _attach_evidence_catalog(chain: ReasoningChain, evidence: list[Candidate]) -> None:
    """Persist evidence id+content so API/UI can resolve citation clicks."""
    catalog = [_catalog_entry(c) for c in evidence[:EVIDENCE_CATALOG_LIMIT]]
    chain.metadata = {**(chain.metadata or {}), "evidence": catalog}


def _catalog_entry(candidate: Candidate) -> dict[str, object]:
    """One catalog row, flagged when its content was cut.

    ``truncated`` is load-bearing downstream: ``eval/metrics_evidence.py``
    re-checks claim↔evidence overlap against *this* persisted text, so a
    supporting token living past the cut would make the eval gate stricter than
    the runtime gate it exists to mirror (BL-13). The flag lets it abstain
    instead of accusing the row of fabricating.
    """
    content = candidate.content or ""
    source = candidate.source
    return {
        "id": candidate.id,
        "content": content[:EVIDENCE_CONTENT_CHARS],
        "truncated": len(content) > EVIDENCE_CONTENT_CHARS,
        "source": source.value,
        "score": candidate.score,
    }
