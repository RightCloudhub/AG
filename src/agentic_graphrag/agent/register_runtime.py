"""Register-guided handlers used by the existing LangGraph query pipeline."""

from __future__ import annotations

import math

from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.options import AgentDeps
from agentic_graphrag.agent.register_answer import answer_from_register
from agentic_graphrag.agent.register_invariants import schedule
from agentic_graphrag.agent.register_llm import (
    initial_context,
    initialization_prompt,
    initialize,
    update,
    update_prompt,
)
from agentic_graphrag.agent.register_progress import (
    context_size,
    progress_state,
    verify_resolutions,
)
from agentic_graphrag.agent.register_search import search, seed_search
from agentic_graphrag.agent.requirements import Register, Requirement, compose
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.reasoning import ReasoningChain, ReasoningStep
from agentic_graphrag.governance.budget import BudgetExceeded


class RegisterRuntime:
    def __init__(self, deps: AgentDeps, guards) -> None:
        self.executor = deps.executor
        self.llm = deps.llm
        self.guards = guards
        self.config = deps.guard_cfg.requirement_register
        self.round_limit = min(self.config.max_rounds, deps.guard_cfg.max_hops)

    def _llm(self, state: AgentState):
        return self.llm if state.get("allow_llm", True) else None

    def enabled(self, state: AgentState) -> bool:
        return self.config.enabled and self._llm(state) is not None

    def node_planner(self, state: AgentState) -> AgentState:
        cache: dict[str, list[str]] = {}
        evidence = {c.id: c for c in _evidence(state)}
        chain = ReasoningChain.model_validate(state["chain"])
        self._seed(state, evidence, chain=chain, cache=cache)
        seed = list(evidence.values())
        register, error, audit = self._initialize(state, seed)
        result = {
            **state,
            "chain": chain.model_dump(),
            "evidence": [c.model_dump() for c in seed],
            "requirement_register": register.snapshot(),
            "register_context": initial_context(register),
            "register_events": [
                {
                    "type": "initialization",
                    "round": 0,
                    "error": error,
                    "register": register.snapshot(),
                    "seed_ids": [c.id for c in seed],
                    **audit,
                }
            ],
            "register_cache": cache,
            "register_seen": [],
            "register_attempts": {},
            "sub_questions": [{"id": r.req_id, "text": r.text} for r in register.items.values()],
        }
        return _snapshot(result)

    def _seed(
        self,
        state: AgentState,
        evidence: dict[str, Candidate],
        *,
        chain: ReasoningChain,
        cache: dict[str, list[str]],
    ) -> None:
        if not self.config.seed_enabled:
            return
        seed, trace = seed_search(
            self.executor,
            state["question"],
            top_k=self.config.seed_top_k,
            tenant_id=state.get("tenant_id", ""),
            cache=cache,
        )
        for hit in seed:
            evidence.setdefault(hit.id, hit)
        chain.steps.append(
            ReasoningStep(
                hop=0,
                sub_question=state["question"],
                tool_calls=[trace],
                evidence_ids=[c.id for c in seed],
            )
        )

    def _initialize(self, state: AgentState, seed: list[Candidate]) -> tuple[Register, str, dict]:
        audit: dict = {}
        try:
            user = initialization_prompt(state["question"], seed, config=self.config)
            messages = [
                {"role": "system", "content": load_prompt("register_system")},
                {"role": "user", "content": user},
            ]
            if context_size(messages) > self.config.context_limit_tokens:
                raise ValueError("seed context limit")
            register = initialize(
                state["question"], seed, self._llm(state), config=self.config, audit=audit
            )
            return register, "", audit
        except Exception as exc:
            register = Register(
                question=state["question"],
                items={
                    "req_0": Requirement(req_id="req_0", text=state["question"], status="active")
                },
            )
            return register, type(exc).__name__, audit

    def node_executor(self, state: AgentState) -> AgentState:
        register = Register.model_validate(state["requirement_register"])
        reason = self._stop_reason(state, register)
        if reason:
            return {**state, "done": True, "register_stop": reason, "register_progress": ""}
        self.guards.on_hop_start()
        if self.guards.state.tripped:
            return {**state, "done": True, "register_stop": self.guards.state.reason}
        return self._retrieve(state, register)

    def _retrieve(self, state: AgentState, register: Register) -> AgentState:
        active = register.search_target(
            next(r for r in register.items.values() if r.status == "active")
        )
        cache = dict(state.get("register_cache", {}))
        hits, traces = search(
            self.executor,
            compose(register.question, active),
            cache,
            tenant_id=state.get("tenant_id", ""),
            anchors=active.anchors,
        )
        evidence = {c.id: c for c in _evidence(state)}
        for hit in hits:
            evidence.setdefault(hit.id, hit)
        seen = set(state.get("register_seen", []))
        fresh = sorted(evidence.keys() - seen)
        attempts = dict(state.get("register_attempts", {}))
        attempts[active.req_id] = attempts.get(active.req_id, 0) + 1
        chain = ReasoningChain.model_validate(state["chain"])
        chain.steps.append(
            ReasoningStep(
                hop=self.guards.state.hop,
                sub_question=active.text,
                depends_on=active.depends_on,
                tool_calls=traces,
                evidence_ids=[c.id for c in hits],
            )
        )
        return _snapshot(
            {
                **state,
                "chain": chain.model_dump(),
                "hop": self.guards.state.hop,
                "register_cache": cache,
                "register_attempts": attempts,
                "register_fresh": fresh,
                "evidence": [c.model_dump() for c in evidence.values()],
                "register_progress": "",
            }
        )

    def node_critic(self, state: AgentState) -> AgentState:
        old = Register.model_validate(state["requirement_register"])
        new = old.model_copy(deep=True)
        evidence = {c.id: c for c in _evidence(state)}
        fresh = [evidence[rid] for rid in state.get("register_fresh", [])]
        context = list(state.get("register_context") or [])
        if state["hop"] == math.ceil(self.config.convergence_ratio * self.round_limit):
            context.append({"role": "user", "content": load_prompt("register_budget")})
        state = {**state, "register_context": context}
        if (
            context_size(context, update_prompt(old.question, fresh))
            > self.config.context_limit_tokens
        ):
            return {**state, "done": True, "register_stop": "context_limit"}
        try:
            new, reports = self._update(state, old, evidence=evidence, fresh=fresh)
        except BudgetExceeded:
            return {**state, "done": True, "register_stop": "llm_budget"}
        except Exception as exc:
            reports = [{"error": type(exc).__name__, "entailed": False}]
        schedule(new, attempts=state.get("register_attempts"))
        result = progress_state(
            state, old, new, verifier=reports, delta_enabled=self.config.delta_enabled
        )
        result["register_seen"] = sorted(
            set(state.get("register_seen", [])) | {c.id for c in fresh}
        )
        chain = ReasoningChain.model_validate(result["chain"])
        chain.steps[-1].conclusion = result["register_progress"] or "No requirement was closed."
        chain.steps[-1].critic_action = "sufficient" if new.is_clear() else "next_hop"
        result["chain"] = chain.model_dump()
        reason = self._stop_reason(result, new)
        result["done"] = bool(reason)
        result["register_stop"] = reason
        return _snapshot(result)

    def _update(
        self,
        state: AgentState,
        old: Register,
        *,
        evidence: dict[str, Candidate],
        fresh: list[Candidate],
    ) -> tuple[Register, list[dict]]:
        llm = self._llm(state)
        if not fresh or not self.config.updates_enabled or llm is None:
            return old.model_copy(deep=True), []
        proposals: list[dict] = []
        new = update(
            old,
            fresh,
            llm,
            context=state["register_context"],
            evidence=evidence,
            config=self.config,
            proposals=proposals,
        )
        reports = []
        if self.config.verifier_enabled:
            reports = verify_resolutions(
                new, old, llm, evidence=evidence, context_limit=self.config.context_limit_tokens
            )
        for proposal in proposals:
            item = new.items.get(proposal["req_id"])
            proposal["accepted"] = item is not None and item.status == "resolved"
        return new, [*reports, *proposals]

    def node_answer(self, state: AgentState) -> AgentState:
        self.guards.check_branch()
        state = {**state, "register_answer_policy": self.config.answer_policy}
        chain = answer_from_register(
            state,
            self.llm,
            allow_llm=self._llm(state) is not None and not self.guards.state.tripped,
            context_limit=self.config.context_limit_tokens,
        )
        return {**state, "chain": chain.model_dump(), "done": True}

    def _stop_reason(self, state: AgentState, register: Register) -> str:
        if register.is_clear():
            return "clear"
        if state.get("hop", 0) >= self.round_limit:
            return "round_budget"
        if context_size(state.get("register_context", [])) > self.config.context_limit_tokens:
            return "context_limit"
        if not register.ready_items():
            return "dependency_blocked"
        return ""


def _evidence(state: AgentState) -> list[Candidate]:
    return [Candidate.model_validate(c) for c in state.get("evidence", [])]


def _snapshot(state: AgentState) -> AgentState:
    memory = MemoryState()
    memory.add_evidence(_evidence(state))
    register = Register.model_validate(state["requirement_register"])
    for item in register.items.values():
        if item.status == "resolved":
            memory.mark_subquestion_done(item.req_id, item.notes)
    return {**state, "memory_snapshot": memory.to_snapshot()}
