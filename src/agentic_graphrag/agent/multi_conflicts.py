"""Contradictory grounded proposals become explicit information needs."""

import hashlib
import json
from collections import defaultdict

from agentic_graphrag.agent.blackboard import Fact, Proposal
from agentic_graphrag.agent.requirements import Register, Requirement
from agentic_graphrag.core.enums import RequirementStatus
from agentic_graphrag.core.evidence import Candidate


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def grounded(fact: Fact, evidence: dict[str, Candidate]) -> bool:
    if not fact.passages or fact.passages - evidence.keys():
        return False
    content = normalize(" ".join(evidence[key].content for key in sorted(fact.passages)))
    return normalize(fact.entity) in content and normalize(fact.value) in content


def add_conflicts(
    proposed: Register,
    proposals: list[Proposal],
    *,
    evidence: dict[str, Candidate],
) -> dict[str, list[str]]:
    groups: dict[tuple[str, str, str], list[tuple[str, Fact]]] = defaultdict(list)
    for proposal in proposals:
        if proposal.requirement.req_id.startswith("conflict_"):
            continue
        for fact in proposal.facts:
            if grounded(fact, evidence):
                key = (normalize(fact.entity), normalize(fact.relation), normalize(fact.scope))
                groups[key].append((proposal.requirement.req_id, fact))
    conflicts: dict[str, list[str]] = {}
    for key, claims in sorted(groups.items()):
        values = sorted({normalize(fact.value) for _, fact in claims})
        if len(values) > 1:
            rid = _add_conflict(proposed, key, claims=claims, values=values)
            conflicts[rid] = sorted({req_id for req_id, _ in claims})
    return conflicts


def _add_conflict(
    register: Register,
    key: tuple[str, str, str],
    *,
    claims: list[tuple[str, Fact]],
    values: list[str],
) -> str:
    digest = hashlib.sha256(json.dumps([key, values]).encode()).hexdigest()[:16]
    rid = f"conflict_{digest}"
    passages = {pid for _, fact in claims for pid in fact.passages}
    if rid not in register.items:
        register.items[rid] = Requirement(
            req_id=rid,
            text=f"Resolve conflict: {key[0]} / {key[1]} ({key[2]}): {' versus '.join(values)}",
            passages=passages,
            touched_passages=passages,
            anchors=[claims[0][1].entity],
            notes="Conflicting supporting passages require disambiguation before answering.",
        )
    if register.items[rid].status != RequirementStatus.RESOLVED:
        for req_id, _ in claims:
            item = register.items[req_id]
            if item.status != RequirementStatus.RETIRED:
                item.status = RequirementStatus.PENDING
    return rid


def blocked_requirements(register: Register, conflicts: dict[str, list[str]]) -> set[str]:
    return {
        req_id
        for rid, req_ids in conflicts.items()
        if register.items[rid].status != RequirementStatus.RESOLVED
        for req_id in req_ids
    }
