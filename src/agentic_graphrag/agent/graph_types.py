"""Typed boundary to the LangGraph runtime and its checkpoint version format."""

from langchain_core.runnables import RunnableConfig as RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agentic_graphrag.agent.state import AgentState

type AgentGraph = CompiledStateGraph[AgentState, None, AgentState, AgentState]
type AgentCheckpointer = BaseCheckpointSaver[str]
