"""Typed boundary to the LangGraph runtime and its checkpoint version format."""

from typing import Protocol

from langchain_core.runnables import RunnableConfig as RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agentic_graphrag.agent.state import AgentState

type AgentGraph = CompiledStateGraph[AgentState, None, AgentState, AgentState]
type AgentCheckpointer = BaseCheckpointSaver[str]


class GraphNodeUpdate(Protocol):
    """LangGraph updates may carry reducer commands alongside typed state values."""

    def __call__(self, state: AgentState) -> dict[str, object]: ...
