"""Blackboard supervisor with LangGraph Send and checkpointed local workers."""

from threading import BoundedSemaphore
from typing import cast

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agentic_graphrag.agent.checkpointer import default_checkpointer
from agentic_graphrag.agent.graph_types import AgentCheckpointer, AgentGraph, RunnableConfig
from agentic_graphrag.agent.multi_merge import merge
from agentic_graphrag.agent.multi_supervisor import bootstrap, supervise
from agentic_graphrag.agent.multi_verifier import verify_batch
from agentic_graphrag.agent.multi_writer import write_answer
from agentic_graphrag.agent.options import AgentDeps
from agentic_graphrag.agent.requirements import compose
from agentic_graphrag.agent.research_state import ResearchOutput, ResearchState
from agentic_graphrag.agent.researcher import Researcher
from agentic_graphrag.agent.state import STATE_SCHEMA_VERSION, AgentState
from agentic_graphrag.agent.state_checkpointer import typed_checkpointer
from agentic_graphrag.config_agent import AgentConfig
from agentic_graphrag.llm.verifier import build_verifier
from agentic_graphrag.observability.branch import branch_span
from agentic_graphrag.observability.context import query_context
from agentic_graphrag.observability.logging_setup import user_id_var


class MultiRuntime:
    def __init__(self, deps: AgentDeps, config: AgentConfig) -> None:
        if deps.llm is None:
            raise ValueError("multi mode requires an explicitly enabled LLM")
        self.deps, self.config, self.llm = deps, config, deps.llm
        self.researcher = Researcher(deps.executor, deps.llm, config=config)
        self.subgraph = self.researcher.build()
        self.verifier = build_verifier(deps.llm, deps.guard_cfg.requirement_register.verifier)
        self.slots = BoundedSemaphore(config.max_parallel)

    def bootstrap(self, state: AgentState) -> AgentState:
        if not state["allow_llm"]:
            raise ValueError("offline queries must use the deterministic graph")
        return bootstrap(
            state, self.deps.executor, self.llm, config=self.config, guards=self.deps.guard_cfg
        )

    def merge(self, state: AgentState) -> AgentState:
        return merge(
            state, rejection_cap=self.deps.guard_cfg.requirement_register.verifier.max_rejections
        )

    def supervisor(self, state: AgentState) -> AgentState:
        self.researcher.cache.prime(state.get("query_cache", {}))
        return supervise(state, config=self.config, guards=self.deps.guard_cfg)

    def dispatch(self, state: AgentState) -> list[Send] | str:
        if not state.get("tasks"):
            return "writer"
        return [
            Send(
                "researcher",
                ResearchState(
                    state_version=STATE_SCHEMA_VERSION,
                    task=task,
                    query=compose(task.question, task.requirement),
                    evidence={
                        key: state["evidence"][key]
                        for key in task.requirement.passages
                        if key in state["evidence"]
                    },
                    receipts=[],
                    calls=0,
                    tools_used=0,
                ),
            )
            for task in state["tasks"]
        ]

    def research(self, state: ResearchState, config: RunnableConfig) -> ResearchOutput:
        with self.slots:
            return self._research(state, config)

    def _research(self, state: ResearchState, config: RunnableConfig) -> ResearchOutput:
        task = state["task"]
        with query_context(
            query_id=task.run_id, tenant_id=task.tenant_id, user_id=user_id_var.get()
        ):
            with branch_span(
                branch_id=task.branch_id,
                req_id=task.requirement.req_id,
                role="researcher",
                model=str(getattr(self.llm, "strong_model", "unknown")),
                tenant_id=task.tenant_id,
                run_id=task.run_id,
            ) as attributes:
                get_stream_writer()(
                    (
                        "sub_question",
                        {
                            "branch_id": task.branch_id,
                            "branch_count": task.branch_count,
                            "req_id": task.requirement.req_id,
                            "hop": task.round,
                            "sub_question": task.requirement.text,
                        },
                    )
                )
                result = cast(ResearchOutput, self.subgraph.invoke(state, config))
                attributes["tokens"] = sum(e.tokens for e in result.get("budget_ledger", []))
                attributes["outcome"] = result["events"][-1].outcome
                return result

    def verify(self, state: AgentState) -> AgentState:
        return verify_batch(state, self.verifier, config=self.config)

    def writer(self, state: AgentState) -> AgentState:
        register = self.deps.guard_cfg.requirement_register
        return write_answer(
            state,
            self.llm,
            config=self.config,
            answer_policy=register.answer_policy,
            context_limit=register.context_limit_tokens,
            guards=self.deps.guard_cfg,
        )


def build_multi_graph(
    deps: AgentDeps, config: AgentConfig, *, checkpointer: AgentCheckpointer | None = None
) -> AgentGraph:
    runtime = MultiRuntime(deps, config)
    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("bootstrap", runtime.bootstrap)
    graph.add_node("merge", runtime.merge)
    graph.add_node("supervisor", runtime.supervisor)
    graph.add_node("researcher", runtime.research, input_schema=ResearchState)
    graph.add_node("verify", runtime.verify)
    graph.add_node("writer", runtime.writer)
    graph.add_edge(START, "bootstrap")
    graph.add_edge("bootstrap", "merge")
    graph.add_edge("merge", "supervisor")
    graph.add_conditional_edges("supervisor", runtime.dispatch, ["researcher", "writer"])
    graph.add_edge("researcher", "verify")
    graph.add_edge("verify", "merge")
    graph.add_edge("writer", END)
    return graph.compile(checkpointer=typed_checkpointer(checkpointer or default_checkpointer()))
