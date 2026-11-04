"""Checkpointed retrieve/reason loop operating on one isolated requirement."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentic_graphrag.agent.blackboard import BranchEvent, Proposal, usage_totals
from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.agent.register_llm import evidence_input, json_text
from agentic_graphrag.agent.research_budget import receipt, remaining_budget
from agentic_graphrag.agent.research_cache import QueryDedup
from agentic_graphrag.agent.research_state import ResearchDecision, ResearchOutput, ResearchState
from agentic_graphrag.agent.research_tools import branch_executor, retrieve
from agentic_graphrag.config import load_prompt
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.core.enums import LLMRole, MessageRole, PromptName, RequirementStatus
from agentic_graphrag.core.ports.llm import LLMClient, Message
from agentic_graphrag.governance.budget import BudgetExceeded, BudgetTracker
from agentic_graphrag.llm.roles import model_name
from agentic_graphrag.llm.scoped import scoped_client
from agentic_graphrag.llm.structured import complete_structured

type ResearchGraph = CompiledStateGraph[ResearchState, None, ResearchState, ResearchOutput]


class Researcher:
    def __init__(
        self,
        executor: Executor,
        llm: LLMClient,
        *,
        config: AgentConfig,
        budget: BudgetTracker | None = None,
    ) -> None:
        self.executor, self.llm, self.config = executor, llm, config
        self.budget = budget
        self.cache = QueryDedup()

    def retrieve(self, state: ResearchState) -> ResearchState:
        task = state["task"]
        receipts = state.get("receipts", [])
        budget = remaining_budget(task, receipts, parent=self.budget)
        used = state.get("tools_used", 0)
        if budget.max_tokens <= 0 or used >= task.tool_limit:
            return {"outcome": "token_budget" if budget.max_tokens <= 0 else "tool_budget"}
        self.cache.prime(state.get("query_cache", {}))
        executor = branch_executor(self.executor, task, budget)
        hits, traces, cache = retrieve(
            executor,
            task,
            self.cache,
            query=state["query"],
            limit=task.tool_limit - used,
        )
        return {
            "evidence": {**state.get("evidence", {}), **hits},
            "query_cache": {**state.get("query_cache", {}), **cache},
            "traces": [*state.get("traces", []), *traces],
            "tools_used": used + len(traces),
            "receipts": [
                *receipts,
                receipt(task, budget, suffix=f"tools:{used}", tool_calls=len(traces)),
            ],
        }

    def reason(self, state: ResearchState) -> ResearchState:
        task = state["task"]
        receipts = state.get("receipts", [])
        budget = remaining_budget(task, receipts, parent=self.budget)
        user = self._prompt(state)
        if budget.max_llm_calls <= 0:
            return {"outcome": "call_budget"}
        if len(user.encode("utf-8")) + self.config.max_output_tokens > budget.max_tokens:
            return {"outcome": "token_budget"}
        llm = scoped_client(self.llm, budget, max_output_tokens=self.config.max_output_tokens)
        calls = state.get("calls", 0)
        try:
            decision = complete_structured(
                llm,
                [Message(role=MessageRole.USER, content=user)],
                ResearchDecision,
                max_retries=0,
                temperature=0,
                role=LLMRole.REGISTER_UPDATE,
            )
        except BudgetExceeded:
            return {
                "outcome": "token_budget",
                "receipts": [*receipts, receipt(task, budget, suffix=f"reason:{calls}", llm=llm)],
            }
        # The branch may propose only its assigned need; ids/text/dependencies are code-owned.
        item = decision.requirement.model_copy(
            update={
                "req_id": task.requirement.req_id,
                "text": task.requirement.text,
                "depends_on": task.requirement.depends_on,
            }
        )
        if item.status == RequirementStatus.RETIRED:
            item.status = RequirementStatus.PENDING
        decision = decision.model_copy(update={"requirement": item})
        return {
            "decision": decision,
            "calls": calls + 1,
            "query": decision.next_query,
            "receipts": [*receipts, receipt(task, budget, suffix=f"reason:{calls}", llm=llm)],
        }

    def _prompt(self, state: ResearchState) -> str:
        task = state["task"]
        decision = state.get("decision")
        item = decision.requirement if decision is not None else task.requirement
        return load_prompt(PromptName.RESEARCHER).format(
            input_json=json_text(
                {
                    "question": task.question,
                    "requirement": item.snapshot(),
                    "known_evidence_ids": task.known_evidence_ids,
                    "passages": evidence_input(list(state.get("evidence", {}).values())),
                }
            )
        )

    def route(self, state: ResearchState) -> str:
        decision = state.get("decision")
        if state.get("outcome") or decision is None:
            return "finish"
        if decision.requirement.status == RequirementStatus.RESOLVED or not decision.next_query:
            return "finish"
        task = state["task"]
        return (
            "retrieve"
            if (
                state.get("tools_used", 0) < task.tool_limit
                and state.get("calls", 0) < task.call_limit
            )
            else "finish"
        )

    def finish(self, state: ResearchState) -> ResearchOutput:
        task = state["task"]
        decision = state.get("decision", ResearchDecision(requirement=task.requirement))
        outcome = state.get("outcome") or decision.requirement.status.value
        entry = Proposal(
            entry_id=f"{task.branch_id}:proposal",
            branch_id=task.branch_id,
            tenant_id=task.tenant_id,
            run_id=task.run_id,
            round=task.round,
            requirement=decision.requirement,
            facts=decision.facts,
            tool_calls=state.get("traces", []),
        )
        return {
            "proposals": [entry],
            "evidence": state.get("evidence", {}),
            "query_cache": state.get("query_cache", {}),
            "budget_ledger": state.get("receipts", []),
            "events": [
                BranchEvent(
                    entry_id=f"{task.branch_id}:done",
                    branch_id=task.branch_id,
                    tenant_id=task.tenant_id,
                    run_id=task.run_id,
                    req_id=task.requirement.req_id,
                    role="researcher",
                    model=model_name(self.llm),
                    round=task.round,
                    outcome=outcome,
                    tokens=usage_totals(state.get("receipts", []))["total_tokens"],
                )
            ],
        }

    def build(self) -> ResearchGraph:
        graph: StateGraph[ResearchState, None, ResearchState, ResearchOutput] = StateGraph(
            ResearchState, output_schema=ResearchOutput
        )
        graph.add_node("retrieve", self.retrieve)
        graph.add_node("reason", self.reason)
        graph.add_node("finish", self.finish)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "reason")
        graph.add_conditional_edges("reason", self.route, ["retrieve", "finish"])
        graph.add_edge("finish", END)
        return graph.compile()
