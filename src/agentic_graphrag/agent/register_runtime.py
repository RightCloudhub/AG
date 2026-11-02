"""Register-guided handlers used by the existing LangGraph query pipeline."""

from __future__ import annotations

import math
from dataclasses import replace

from agentic_graphrag.agent.guardrails import Guardrails
from agentic_graphrag.agent.options import AgentDeps
from agentic_graphrag.agent.plan_dag import SubQuestion
from agentic_graphrag.agent.register_answer import answer_from_register
from agentic_graphrag.agent.register_helpers import (
    finish_register_round,
    stopped_state,
    with_register_memory,
)
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
from agentic_graphrag.agent.register_state import (
    InitializationAudit,
    InitializationEvent,
    RegisterRuntimeState,
    VerificationEvent,
)
from agentic_graphrag.agent.requirements import Register, Requirement, compose
from agentic_graphrag.agent.state import AgentState, active_register
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import MessageRole, PromptName, RequirementStatus
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain, ReasoningStep
from agentic_graphrag.governance.budget import BudgetExceeded


class RegisterRuntime:
    def __init__(self, deps: AgentDeps, guards: Guardrails) -> None:
        self.executor = deps.executor
        self.llm = deps.llm
        self.guards = guards
        self.config = deps.guard_cfg.requirement_register
        self.round_limit = min(self.config.max_rounds, deps.guard_cfg.max_hops)

    def _llm(self, state: AgentState) -> LLMClient | None:
        return self.llm if state.get("allow_llm", True) else None

    def enabled(self, state: AgentState) -> bool:
        return self.config.enabled and self._llm(state) is not None

    def node_planner(self, state: AgentState) -> AgentState:
        cache: dict[str, list[str]] = {}
        evidence = dict(state.get("evidence", {}))
        chain = state["chain"].model_copy(deep=True)
        self._seed(state, evidence, chain=chain, cache=cache)
        seed = list(evidence.values())
        register, error, audit = self._initialize(state, seed)
        event: InitializationEvent = {
            "type": "initialization",
            "round": 0,
            "error": error,
            "register": register,
            "seed_ids": [c.id for c in seed],
            **audit,
        }
        result: AgentState = {
            **state,
            "chain": chain,
            "evidence": evidence,
            "register": register,
            "register_runtime": RegisterRuntimeState(
                context=initial_context(register), events=[event], cache=cache
            ),
            "sub_questions": [
                SubQuestion(id=r.req_id, text=r.text) for r in register.items.values()
            ],
        }
        return with_register_memory(result)

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

    def _initialize(
        self, state: AgentState, seed: list[Candidate]
    ) -> tuple[Register, str, InitializationAudit]:
        audit: InitializationAudit = {}
        try:
            user = initialization_prompt(state["question"], seed, config=self.config)
            messages = [
                {"role": MessageRole.SYSTEM, "content": load_prompt(PromptName.REGISTER_SYSTEM)},
                {"role": MessageRole.USER, "content": user},
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
                    "req_0": Requirement(
                        req_id="req_0", text=state["question"], status=RequirementStatus.ACTIVE
                    )
                },
            )
            return register, type(exc).__name__, audit

    def node_executor(self, state: AgentState) -> AgentState:
        register = active_register(state)
        reason = self._stop_reason(state, register)
        if reason:
            return stopped_state(state, reason, clear_progress=True)
        self.guards.on_hop_start()
        if self.guards.state.tripped:
            return stopped_state(state, self.guards.state.reason)
        return self._retrieve(state, register)

    def _retrieve(self, state: AgentState, register: Register) -> AgentState:
        active = register.search_target(
            next(r for r in register.items.values() if r.status == RequirementStatus.ACTIVE)
        )
        runtime = state["register_runtime"]
        cache = dict(runtime.cache)
        hits, traces = search(
            self.executor,
            compose(register.question, active),
            cache,
            tenant_id=state.get("tenant_id", ""),
            anchors=active.anchors,
        )
        evidence = dict(state.get("evidence", {}))
        for hit in hits:
            evidence.setdefault(hit.id, hit)
        seen = set(runtime.seen)
        fresh = sorted(evidence.keys() - seen)
        attempts = dict(runtime.attempts)
        attempts[active.req_id] = attempts.get(active.req_id, 0) + 1
        chain = state["chain"].model_copy(deep=True)
        chain.steps.append(
            ReasoningStep(
                hop=self.guards.state.hop,
                sub_question=active.text,
                depends_on=active.depends_on,
                tool_calls=traces,
                evidence_ids=[c.id for c in hits],
            )
        )
        return with_register_memory(
            {
                **state,
                "chain": chain,
                "hop": self.guards.state.hop,
                "register_runtime": replace(
                    runtime, cache=cache, attempts=attempts, fresh=fresh, progress=""
                ),
                "evidence": evidence,
            }
        )

    def node_critic(self, state: AgentState) -> AgentState:
        old = active_register(state)
        new = old.model_copy(deep=True)
        evidence = state.get("evidence", {})
        runtime = state["register_runtime"]
        fresh = [evidence[rid] for rid in runtime.fresh]
        context = list(runtime.context)
        if state["hop"] == math.ceil(self.config.convergence_ratio * self.round_limit):
            context.append(
                {"role": MessageRole.USER, "content": load_prompt(PromptName.REGISTER_BUDGET)}
            )
        state = {**state, "register_runtime": replace(runtime, context=context)}
        if (
            context_size(context, update_prompt(old.question, fresh))
            > self.config.context_limit_tokens
        ):
            return stopped_state(state, "context_limit")
        try:
            new, reports = self._update(state, old, evidence=evidence, fresh=fresh)
        except BudgetExceeded:
            return stopped_state(state, "llm_budget")
        except Exception as exc:
            reports = [{"error": type(exc).__name__, "entailed": False}]
        schedule(new, attempts=runtime.attempts)
        result = progress_state(
            state, old, new, verifier=reports, delta_enabled=self.config.delta_enabled
        )
        return finish_register_round(
            result, new, fresh, seen=runtime.seen, stop=self._stop_reason(result, new)
        )

    def _update(
        self,
        state: AgentState,
        old: Register,
        *,
        evidence: dict[str, Candidate],
        fresh: list[Candidate],
    ) -> tuple[Register, list[VerificationEvent]]:
        llm = self._llm(state)
        if not fresh or not self.config.updates_enabled or llm is None:
            return old.model_copy(deep=True), []
        proposals: list[VerificationEvent] = []
        new = update(
            old,
            fresh,
            llm,
            context=state["register_runtime"].context,
            evidence=evidence,
            config=self.config,
            proposals=proposals,
        )
        reports: list[VerificationEvent] = []
        if self.config.verifier_enabled:
            reports = verify_resolutions(
                new, old, llm, evidence=evidence, context_limit=self.config.context_limit_tokens
            )
        for proposal in proposals:
            item = new.items.get(proposal["req_id"])
            proposal["accepted"] = item is not None and item.status == RequirementStatus.RESOLVED
        return new, [*reports, *proposals]

    def node_answer(self, state: AgentState) -> AgentState:
        self.guards.check_branch()
        state = {
            **state,
            "register_runtime": replace(
                state["register_runtime"], answer_policy=self.config.answer_policy
            ),
        }
        chain = answer_from_register(
            state,
            self.llm,
            allow_llm=self._llm(state) is not None and not self.guards.state.tripped,
            context_limit=self.config.context_limit_tokens,
        )
        return {**state, "chain": chain, "done": True}

    def _stop_reason(self, state: AgentState, register: Register) -> str:
        if register.is_clear():
            return "clear"
        if state.get("hop", 0) >= self.round_limit:
            return "round_budget"
        if context_size(state["register_runtime"].context) > self.config.context_limit_tokens:
            return "context_limit"
        if not register.ready_items():
            return "dependency_blocked"
        return ""
