"""Agent loop node implementations (planner / executor / critic / answer).

StateGraph assembly and public entrypoint live in
:mod:`agentic_graphrag.agent.loop`.
"""

from __future__ import annotations

from typing import Any, TypedDict

from agentic_graphrag.agent.critic import critique
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.agent.loop_handlers import (
    CriticApplyCtx,
    ExecutorNodeCtx,
    apply_critic_result,
    execute_subquestion,
    load_evidence,
    load_evidence_for_critic,
    materialize_current,
    skip_excluded_or_duplicate,
)
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.options import AgentDeps, CritiqueContext
from agentic_graphrag.agent.planner import SubQuestion, plan
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.trace import ReasoningChain
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider


class AgentState(TypedDict, total=False):
    """LangGraph typed state (P2-AG-03). Memory semantics live in MemoryState."""

    question: str
    chain: dict[str, Any]
    sub_questions: list[dict[str, Any]]
    current_index: int
    hop: int
    evidence: list[dict[str, Any]]
    memory_summary: str
    memory_snapshot: dict[str, Any]
    done: bool
    guardrail_status: str
    allow_llm: bool


class AgentRuntime:
    """Holds per-run memory/guardrails and implements StateGraph node handlers."""

    def __init__(
        self,
        executor: Executor,
        llm: LLMProvider | None,
        guard_cfg: GuardrailConfig,
        *,
        budget: BudgetTracker | None = None,
        deps: AgentDeps | None = None,
    ) -> None:
        if deps is not None:
            executor = deps.executor
            llm = deps.llm
            guard_cfg = deps.guard_cfg
            budget = deps.budget
        self.executor = executor
        self.llm = llm
        self.guard_cfg = guard_cfg
        self.budget = budget
        self.guards = Guardrails(guard_cfg, budget=budget)
        self.memory = MemoryState()

    def _hydrate_from_state(self, state: AgentState) -> None:
        """Restore Memory (and hop floor) from checkpointer-backed state."""
        snap = state.get("memory_snapshot")
        if snap:
            self.memory = MemoryState.from_snapshot(snap)
        hop = int(state.get("hop") or 0)
        if hop > self.guards.state.hop:
            self.guards.state.hop = hop
        status = str(state.get("guardrail_status") or "")
        if status.startswith("tripped") or "tripped" in status.lower():
            self.guards.state.tripped = True
            if not self.guards.state.reason:
                self.guards.state.reason = status

    def node_planner(self, state: AgentState) -> AgentState:
        self._hydrate_from_state(state)
        allow_llm = bool(state.get("allow_llm", True))
        known = list(self.executor.known_entities or [])
        sqs = plan(
            state["question"],
            self.memory.summary(),
            self.llm if allow_llm else None,
            allow_llm=allow_llm and self.llm is not None,
            known_entities=known,
        )
        return {
            **state,
            "sub_questions": [s.model_dump() for s in sqs],
            "current_index": 0,
            "hop": 0,
            "done": False,
            "memory_snapshot": self.memory.to_snapshot(),
        }

    def node_executor(self, state: AgentState) -> AgentState:
        self._hydrate_from_state(state)
        self.guards.on_hop_start()
        if self.guards.state.tripped:
            return {
                **state,
                "done": True,
                "guardrail_status": self.guards.status_text(),
            }

        sqs = list(state.get("sub_questions") or [])
        idx = int(state.get("current_index") or 0)
        if idx >= len(sqs):
            return {**state, "done": True}

        sq, sqs = materialize_current(sqs, idx, self.memory)
        ctx = ExecutorNodeCtx(
            state=state,
            sq=sq,
            sqs=sqs,
            idx=idx,
            memory=self.memory,
            guards=self.guards,
            executor=self.executor,
            llm=self.llm,
        )
        skipped = skip_excluded_or_duplicate(ctx)
        if skipped is not None:
            return skipped
        return execute_subquestion(ctx)

    def node_critic(self, state: AgentState) -> AgentState:
        self._hydrate_from_state(state)
        if state.get("done"):
            return state

        sqs = list(state.get("sub_questions") or [])
        idx = int(state.get("current_index") or 0)
        sq = SubQuestion.model_validate(sqs[idx]) if idx < len(sqs) else None
        sq_text = sq.text if sq else state["question"]
        sq_id = sq.id if sq else f"sq{idx}"
        remaining = max(0, len(sqs) - idx - 1)
        allow_llm = bool(state.get("allow_llm", True))

        result = critique(
            CritiqueContext(
                question=state["question"],
                sub_question=sq_text,
                evidence=load_evidence_for_critic(state),
                explored_paths=sorted(self.memory.explored_paths),
                hop=int(state.get("hop") or 1),
                max_hops=self.guard_cfg.max_hops,
                remaining_subquestions=remaining,
                excluded_hypotheses=sorted(self.memory.excluded_hypotheses),
            ),
            self.llm if allow_llm else None,
            allow_llm=allow_llm and self.llm is not None,
        )
        return apply_critic_result(
            CriticApplyCtx(
                state=state,
                result=result,
                sq=sq,
                sq_text=sq_text,
                sq_id=sq_id,
                idx=idx,
                remaining=remaining,
                memory=self.memory,
                guards=self.guards,
                guard_cfg=self.guard_cfg,
            )
        )

    def node_answer(self, state: AgentState) -> AgentState:
        self._hydrate_from_state(state)
        chain = ReasoningChain.model_validate(state["chain"])
        evidence = load_evidence(state)
        allow_llm = bool(state.get("allow_llm", True))
        self._apply_budget_to_chain(chain)

        if self.guards.state.tripped and not evidence:
            chain.honest_fallback(self.guards.state.reason or "guardrail tripped")
        else:
            chain = generate_answer(
                chain,
                evidence,
                self.llm,
                conclusions="; ".join(self.memory.conclusions),
                guardrail_status=str(state.get("guardrail_status") or self.guards.status_text()),
                allow_llm=allow_llm and self.llm is not None,
            )

        chain.explored_paths = sorted(self.memory.explored_paths)
        return {
            **state,
            "chain": chain.model_dump(),
            "done": True,
            "memory_snapshot": self.memory.to_snapshot(),
        }

    def _apply_budget_to_chain(self, chain: ReasoningChain) -> None:
        if not self.budget:
            return
        snap = self.budget.snapshot()
        chain.cost.llm_calls = snap["llm_calls"]
        chain.cost.tokens = snap["total_tokens"]
        chain.cost.prompt_tokens = snap["prompt_tokens"]
        chain.cost.completion_tokens = snap["completion_tokens"]

    def route_after_critic(self, state: AgentState) -> str:
        if state.get("done") or self.guards.state.tripped:
            return "answer"
        return "executor"
