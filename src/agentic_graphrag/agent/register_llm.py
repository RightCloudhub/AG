"""Seed initialization, incremental updates and an independent sufficiency prompt."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from agentic_graphrag.agent.register_invariants import apply_invariants
from agentic_graphrag.agent.requirements import Register, RegisterPayload, Requirement
from agentic_graphrag.config import load_prompt
from agentic_graphrag.config_register import RequirementRegisterConfig
from agentic_graphrag.core.enums import MessageRole, PromptName, RequirementStatus
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.json_types import JsonObject
from agentic_graphrag.core.ports.llm import LLMClient, Message, Tier
from agentic_graphrag.llm.structured import complete_structured


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entailed: bool
    reason: str


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def evidence_input(evidence: list[Candidate]) -> list[JsonObject]:
    return [{"id": c.id, "content": c.content} for c in evidence]


def initialize(
    question: str,
    seed: list[Candidate],
    llm: LLMClient | None,
    *,
    config: RequirementRegisterConfig,
    audit: JsonObject | None = None,
) -> Register:
    empty = Register(question=question)
    if llm is None:
        proposed = Register(question=question, items={"req_0": Requirement(text=question)})
    else:
        user = initialization_prompt(question, seed, config=config)
        payload = complete_structured(
            llm, _messages(user), RegisterPayload, temperature=0, max_retries=0
        )
        if audit is not None:
            audit.update(
                evidence_summary=payload.evidence_summary, reasoning_note=payload.reasoning_note
            )
        proposed = Register(question=question, items=payload.requirement_register)
    if not proposed.items or len(proposed.items) > config.max_requirements:
        raise ValueError("invalid initial requirement count")
    for item in proposed.items.values():
        item.status = (
            RequirementStatus.ACTIVE
            if item.status == RequirementStatus.ACTIVE
            else RequirementStatus.PENDING
        )
    return apply_invariants(proposed, empty, evidence={c.id: c for c in seed})


def initialization_prompt(
    question: str, seed: list[Candidate], *, config: RequirementRegisterConfig
) -> str:
    return load_prompt(PromptName.REGISTER_INIT).format(
        max_requirements=config.max_requirements,
        input_json=json_text({"question": question, "seed": evidence_input(seed)}),
    )


def initial_context(register: Register) -> list[dict[str, str]]:
    user = load_prompt(PromptName.REGISTER_STATE).format(
        register_json=json_text(register.snapshot())
    )
    return [
        {"role": MessageRole.SYSTEM, "content": load_prompt(PromptName.REGISTER_SYSTEM)},
        {"role": MessageRole.USER, "content": user},
    ]


def update(
    register: Register,
    fresh: list[Candidate],
    llm: LLMClient,
    *,
    context: list[dict[str, str]],
    evidence: dict[str, Candidate],
    config: RequirementRegisterConfig,
    proposals: list[JsonObject] | None = None,
) -> Register:
    user = update_prompt(register.question, fresh)
    messages = [Message(role=MessageRole(m["role"]), content=m["content"]) for m in context]
    messages.append(Message(role=MessageRole.USER, content=user))
    payload = complete_structured(llm, messages, RegisterPayload, temperature=0, max_retries=0)
    proposed = Register(question=register.question, items=payload.requirement_register)
    if proposals is not None:
        proposals.extend(
            {"type": "resolution_proposal", "req_id": rid, "passages": sorted(r.passages)}
            for rid, r in proposed.items.items()
            if r.status == RequirementStatus.RESOLVED
        )
    if len(set(proposed.items) | register.items.keys()) > config.max_requirements:
        raise ValueError("requirement count exceeds limit")
    fresh_ids = {c.id for c in fresh}
    proposed.items = {
        rid: r
        for rid, r in proposed.items.items()
        if rid in register.items or r.touched_passages & fresh_ids
    }
    return apply_invariants(proposed, register, evidence=evidence, fresh_ids=fresh_ids)


def update_prompt(question: str, fresh: list[Candidate]) -> str:
    return load_prompt(PromptName.REGISTER_UPDATE).format(
        input_json=json_text({"question": question, "new_evidence": evidence_input(fresh)})
    )


def verify(item: Requirement, evidence: dict[str, Candidate], llm: LLMClient) -> Verification:
    supporting = [evidence[i] for i in sorted(item.passages)]
    user = load_prompt(PromptName.REGISTER_VERIFY).format(
        input_json=json_text({"requirement": item.text, "passages": evidence_input(supporting)})
    )
    return complete_structured(
        llm, _messages(user), Verification, tier=Tier.LIGHT, temperature=0, max_retries=0
    )


def _messages(user: str) -> list[Message]:
    return [
        Message(role=MessageRole.SYSTEM, content=load_prompt(PromptName.REGISTER_SYSTEM)),
        Message(role=MessageRole.USER, content=user),
    ]
