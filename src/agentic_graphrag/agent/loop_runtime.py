"""Agent nodes; StateGraph assembly lives in :mod:`agentic_graphrag.agent.loop`."""

from __future__ import annotations

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
from agentic_graphrag.agent.parallel_branches import is_prefetched
from agentic_graphrag.agent.plan_coverage import annotate_plan_coverage
from agentic_graphrag.agent.plan_dag import cap_plan_breadth
from agentic_graphrag.agent.planner import SubQuestion, plan
from agentic_graphrag.agent.register_runtime import RegisterRuntime
from agentic_graphrag.agent.state import AgentState
from agentic_graphrag.core.enums import AgentNode
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.verdict import evidence_gap
from agentic_graphrag.governance.budget import BudgetTracker

# Answer → executor back-edges per run, and new sub-questions per back-edge.
MAX_ANSWER_RETRIEVALS = 1
MAX_GAP_SUB_QUESTIONS = 2


class AgentRuntime:
    """Holds per-run memory/guardrails and implements StateGraph node handlers."""

    def __init__(
        self,
        executor: Executor,
        llm: LLMClient | None,
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
        self.register_runtime = RegisterRuntime(
            AgentDeps(executor, llm, guard_cfg, budget), self.guards
        )

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
        if self.register_runtime.enabled(state):
            return self.register_runtime.node_planner(state)
        allow_llm = bool(state.get("allow_llm", True))
        known = list(self.executor.known_entities or [])
        sqs = plan(
            state["question"],
            self.memory.summary(),
            self.llm if allow_llm else None,
            allow_llm=allow_llm and self.llm is not None,
            known_entities=known,
        )
        # Enforce breadth explicitly instead of silently truncating the tail (BL-12).
        kept, dropped = cap_plan_breadth(sqs, self.guard_cfg.max_sub_questions)
        self.guards.note_plan_breadth(len(sqs))
        return {
            **state,
            "sub_questions": [s.model_dump() for s in kept],
            "dropped_sub_questions": [s.text for s in dropped],
            "current_index": 0,
            "hop": 0,
            "done": False,
            "memory_snapshot": self.memory.to_snapshot(),
        }

    def node_executor(self, state: AgentState) -> AgentState:
        self._hydrate_from_state(state)
        if self.register_runtime.enabled(state):
            return self.register_runtime.node_executor(state)
        self._start_node(state)
        hop = self.guards.state.hop
        if self.guards.state.tripped:
            return {
                **state,
                "hop": hop,
                "done": True,
                "guardrail_status": self.guards.status_text(),
                "memory_snapshot": self.memory.to_snapshot(),
            }

        sqs = list(state.get("sub_questions") or [])
        idx = int(state.get("current_index") or 0)
        if idx >= len(sqs):
            return {
                **state,
                "hop": hop,
                "done": True,
                "guardrail_status": self.guards.status_text(),
                "memory_snapshot": self.memory.to_snapshot(),
            }

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

    def _start_node(self, state: AgentState) -> None:
        """A node whose branch was retrieved concurrently stays on the current hop:
        hops measure dependency depth, not breadth (BL-12)."""
        sqs = list(state.get("sub_questions") or [])
        idx = int(state.get("current_index") or 0)
        if idx < len(sqs) and is_prefetched(state, str(sqs[idx].get("id"))):
            self.guards.check_branch()
        else:
            self.guards.on_hop_start()

    def route_after_executor(self, state: AgentState) -> AgentNode:
        """Skip critic when hop already terminal (saves recursion budget)."""
        if state.get("done") or self.guards.state.tripped:
            return AgentNode.ANSWER
        if int(state.get("hop") or 0) > self.guard_cfg.max_hops:
            return AgentNode.ANSWER
        return AgentNode.CRITIC

    def node_critic(self, state: AgentState) -> AgentState:
        self._hydrate_from_state(state)
        if self.register_runtime.enabled(state):
            return self.register_runtime.node_critic(state)
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
                critic_mode=self.guard_cfg.critic_mode,
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
        if self.register_runtime.enabled(state):
            return self.register_runtime.node_answer(state)
        chain = ReasoningChain.model_validate(state["chain"])
        evidence = load_evidence(state)
        allow_llm = bool(state.get("allow_llm", True))
        self._apply_budget_to_chain(chain)

        can_retrieve = self._can_retrieve_again(state)
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
                report_evidence_gaps=can_retrieve,
            )

        gap = evidence_gap(chain) if can_retrieve else None
        if gap is not None and gap.reason != "partial_answer":
            # Refactor §1.5: answer → executor back-edge for missing evidence.
            return self._loop_back_for_gap(state, chain, gap.missing)

        chain.explored_paths = sorted(self.memory.explored_paths)
        annotate_plan_coverage(chain, state)
        return {
            **state,
            "chain": chain.model_dump(),
            "done": True,
            "memory_snapshot": self.memory.to_snapshot(),
        }

    def _can_retrieve_again(self, state: AgentState) -> bool:
        if self.guards.state.tripped:
            return False
        if int(state.get("answer_retries") or 0) >= MAX_ANSWER_RETRIEVALS:
            return False
        return int(state.get("hop") or 0) < self.guard_cfg.max_hops

    def _loop_back_for_gap(
        self, state: AgentState, chain: ReasoningChain, missing: list[str]
    ) -> AgentState:
        sqs = list(state.get("sub_questions") or [])
        start = len(sqs)
        for i, text in enumerate(m for m in missing[:MAX_GAP_SUB_QUESTIONS] if m.strip()):
            sqs.append(
                SubQuestion(
                    id=f"gap{start + i}", text=text.strip(), rationale="evidence_gap"
                ).model_dump()
            )
        meta = dict(chain.metadata or {})
        meta.pop("evidence_gap", None)
        meta["evidence_gap_loops"] = int(meta.get("evidence_gap_loops") or 0) + 1
        chain.metadata = meta
        return {
            **state,
            "chain": chain.model_dump(),
            "sub_questions": sqs,
            "current_index": start,
            "done": False,
            "answer_retries": int(state.get("answer_retries") or 0) + 1,
            "evidence_gap": {"missing": missing},
            "memory_snapshot": self.memory.to_snapshot(),
        }

    def route_after_answer(self, state: AgentState) -> AgentNode:
        return AgentNode.END if state.get("done") else AgentNode.EXECUTOR

    def _apply_budget_to_chain(self, chain: ReasoningChain) -> None:
        if not self.budget:
            return
        snap = self.budget.snapshot()
        chain.cost.llm_calls = snap["llm_calls"]
        chain.cost.tokens = snap["total_tokens"]
        chain.cost.prompt_tokens = snap["prompt_tokens"]
        chain.cost.completion_tokens = snap["completion_tokens"]

    def route_after_critic(self, state: AgentState) -> AgentNode:
        if state.get("done") or self.guards.state.tripped:
            return AgentNode.ANSWER
        # Belt-and-suspenders: hop cap must end the loop even if done was lost.
        if int(state.get("hop") or 0) >= self.guard_cfg.max_hops:
            return AgentNode.ANSWER
        if self.guards.state.hop >= self.guard_cfg.max_hops:
            return AgentNode.ANSWER
        return AgentNode.EXECUTOR
