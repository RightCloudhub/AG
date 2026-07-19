"""Agent loop node implementations (planner / executor / critic / answer).

StateGraph assembly and public entrypoint live in
:mod:`agentic_graphrag.agent.loop`.
"""

from __future__ import annotations

from typing import Any, TypedDict

from agentic_graphrag.agent.critic import CriticAction, critique
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails
from agentic_graphrag.agent.memory import MemoryState
from agentic_graphrag.agent.planner import SubQuestion, materialize_subquestion, plan
from agentic_graphrag.generation.answer import generate_answer
from agentic_graphrag.generation.trace import ReasoningChain, ReasoningStep
from agentic_graphrag.llm.budget import BudgetTracker
from agentic_graphrag.llm.provider import LLMProvider
from agentic_graphrag.retrieval.contracts import Candidate


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
        budget: BudgetTracker | None = None,
    ) -> None:
        self.executor = executor
        self.llm = llm
        self.guard_cfg = guard_cfg
        self.budget = budget
        self.guards = Guardrails(guard_cfg, budget=budget)
        self.memory = MemoryState()

    def node_planner(self, state: AgentState) -> AgentState:
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
        guards = self.guards
        memory = self.memory
        executor = self.executor
        llm = self.llm

        guards.on_hop_start()
        if guards.state.tripped:
            return {**state, "done": True, "guardrail_status": guards.status_text()}

        sqs = list(state.get("sub_questions") or [])
        idx = int(state.get("current_index") or 0)
        if idx >= len(sqs):
            return {**state, "done": True}

        sq = SubQuestion.model_validate(sqs[idx])
        # P2-AG-01: materialize placeholders from prior conclusions
        sq = materialize_subquestion(sq, memory.conclusions_by_subquestion)
        sqs[idx] = sq.model_dump()

        if memory.is_excluded(sq.text):
            memory.mark_subquestion_done(sq.id)
            return {
                **state,
                "sub_questions": sqs,
                "current_index": idx + 1,
                "done": idx + 1 >= len(sqs),
                "guardrail_status": guards.status_text(),
                "memory_snapshot": memory.to_snapshot(),
            }

        if memory.is_duplicate_subquestion(sq.text) and idx > 0:
            memory.exclude_hypothesis(sq.text)
            return {
                **state,
                "sub_questions": sqs,
                "current_index": idx + 1,
                "guardrail_status": guards.status_text(),
                "done": idx + 1 >= len(sqs),
                "memory_snapshot": memory.to_snapshot(),
            }
        memory.mark_subquestion(sq.text)

        allow_llm = bool(state.get("allow_llm", True))
        from agentic_graphrag.agent.entities import extract_entity_mentions

        hints = extract_entity_mentions(
            state["question"] + " " + sq.text,
            executor.known_entities or None,
        )
        # Prefer entities mentioned in materialized conclusion text
        for conc in memory.conclusions_by_subquestion.values():
            hints.extend(extract_entity_mentions(conc, executor.known_entities or None))
        candidates, traces = executor.run(
            sq.text,
            entities_hint=hints,
            allow_llm=allow_llm and llm is not None,
        )
        added = memory.add_evidence(candidates)

        step = ReasoningStep(
            hop=guards.state.hop,
            sub_question=sq.text,
            depends_on=sq.depends_on,
            tool_calls=traces,
            evidence_ids=added,
        )
        chain = ReasoningChain.model_validate(state["chain"])
        chain.steps.append(step)
        chain.explored_paths = sorted(memory.explored_paths)

        return {
            **state,
            "sub_questions": sqs,
            "hop": guards.state.hop,
            "evidence": [c.model_dump() for c in memory.evidence_list()],
            "chain": chain.model_dump(),
            "memory_summary": memory.summary(),
            "memory_snapshot": memory.to_snapshot(),
            "guardrail_status": guards.status_text(),
        }

    def node_critic(self, state: AgentState) -> AgentState:
        if state.get("done"):
            return state
        guards = self.guards
        memory = self.memory
        guard_cfg = self.guard_cfg
        llm = self.llm

        sqs = list(state.get("sub_questions") or [])
        idx = int(state.get("current_index") or 0)
        sq = SubQuestion.model_validate(sqs[idx]) if idx < len(sqs) else None
        sq_text = sq.text if sq else state["question"]
        sq_id = sq.id if sq else f"sq{idx}"
        evidence = [Candidate.model_validate(e) for e in state.get("evidence") or []]
        allow_llm = bool(state.get("allow_llm", True))
        remaining = max(0, len(sqs) - idx - 1)

        result = critique(
            state["question"],
            sq_text,
            evidence,
            sorted(memory.explored_paths),
            llm if allow_llm else None,
            allow_llm=allow_llm and llm is not None,
            hop=int(state.get("hop") or 1),
            max_hops=guard_cfg.max_hops,
            remaining_subquestions=remaining,
            excluded_hypotheses=sorted(memory.excluded_hypotheses),
        )

        chain = ReasoningChain.model_validate(state["chain"])
        conclusion = result.partial_answer or ""
        if chain.steps:
            chain.steps[-1].critic_action = result.action.value
            if conclusion:
                chain.steps[-1].conclusion = conclusion

        # Record per-sub-question conclusion for placeholder materialization
        if result.sub_answered or result.action == CriticAction.SUFFICIENT or conclusion:
            memory.mark_subquestion_done(sq_id, conclusion or None)

        new_state: AgentState = {
            **state,
            "chain": chain.model_dump(),
            "guardrail_status": guards.status_text(),
            "memory_snapshot": memory.to_snapshot(),
            "memory_summary": memory.summary(),
        }

        # Planned DAG: advance while more nodes remain (sub-level sufficient)
        if remaining > 0 and not guards.state.tripped:
            new_state["current_index"] = idx + 1
            new_state["done"] = False
            if guards.state.hop >= guard_cfg.max_hops:
                new_state["done"] = True
            return new_state

        if result.action == CriticAction.SUFFICIENT and result.global_answered:
            new_state["done"] = True
        elif result.action == CriticAction.SUFFICIENT:
            # Sub answered, global not — if no remaining plan, still finish
            new_state["done"] = True
        elif result.action == CriticAction.GIVE_UP or guards.state.tripped:
            if result.action == CriticAction.GIVE_UP:
                memory.exclude_hypothesis(sq_text)
            new_state["done"] = True
        elif result.action in (CriticAction.NEXT_HOP, CriticAction.REWRITE):
            new_sq = result.new_sub_question or sq_text
            if result.action == CriticAction.REWRITE:
                memory.exclude_hypothesis(sq_text)
            if memory.is_duplicate_subquestion(new_sq) or memory.is_excluded(new_sq):
                new_state["done"] = True
            else:
                new_id = f"sq_dyn_{len(sqs) + 1}"
                sqs = list(sqs) + [
                    SubQuestion(
                        id=new_id,
                        text=new_sq,
                        depends_on=[sq_id] if sq else [],
                        rationale=result.rationale,
                    ).model_dump()
                ]
                new_state["sub_questions"] = sqs
                new_state["current_index"] = len(sqs) - 1
        else:
            new_state["current_index"] = idx + 1
            if new_state["current_index"] >= len(sqs):
                new_state["done"] = True

        if guards.state.hop >= guard_cfg.max_hops:
            new_state["done"] = True
            new_state["guardrail_status"] = guards.status_text()

        return new_state

    def node_answer(self, state: AgentState) -> AgentState:
        chain = ReasoningChain.model_validate(state["chain"])
        evidence = [Candidate.model_validate(e) for e in state.get("evidence") or []]
        allow_llm = bool(state.get("allow_llm", True))
        if self.budget:
            snap = self.budget.snapshot()
            chain.cost.llm_calls = snap["llm_calls"]
            chain.cost.tokens = snap["total_tokens"]
            chain.cost.prompt_tokens = snap["prompt_tokens"]
            chain.cost.completion_tokens = snap["completion_tokens"]

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

    def route_after_critic(self, state: AgentState) -> str:
        if state.get("done") or self.guards.state.tripped:
            return "answer"
        return "executor"
