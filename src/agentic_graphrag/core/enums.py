"""Closed internal vocabularies shared by domain and transport adapters."""

from enum import StrEnum
from typing import NewType

ExternalToolName = NewType("ExternalToolName", str)


class AgentNode(StrEnum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    CRITIC = "critic"
    ANSWER = "answer"
    END = "end"


class RetrievalTool(StrEnum):
    GRAPH_NEIGHBORS = "graph_neighbors"
    GRAPH_PATH = "graph_path"
    GRAPH_SUBGRAPH = "graph_subgraph"
    VECTOR_SEARCH = "vector_search"
    FULLTEXT_SEARCH = "fulltext_search"
    CACHE = "cache"


type ToolName = RetrievalTool | ExternalToolName


GRAPH_TOOLS = frozenset(
    {RetrievalTool.GRAPH_NEIGHBORS, RetrievalTool.GRAPH_PATH, RetrievalTool.GRAPH_SUBGRAPH}
)


class ToolArgument(StrEnum):
    ENTITY = "entity"
    NAME = "name"
    SOURCE = "source"
    TARGET = "target"
    ENTITIES = "entities"
    SEEDS = "seeds"
    QUERY = "query"
    MAX_HOPS = "max_hops"
    RELATION_TYPES = "relation_types"
    TOP_K = "top_k"


class CriticMode(StrEnum):
    LLM = "llm"
    TIERED = "tiered"
    OFFLINE = "offline"


class CriticAction(StrEnum):
    SUFFICIENT = "sufficient"
    NEXT_HOP = "next_hop"
    REWRITE = "rewrite"
    GIVE_UP = "give_up"


class StepAction(StrEnum):
    FAST_PATH = "fast_path"


class FusionMethod(StrEnum):
    RRF = "rrf"
    CONCAT = "concat"


class RerankKind(StrEnum):
    NONE = "none"
    LEXICAL = "lexical"
    HTTP = "http"


class RetrievalChannel(StrEnum):
    VECTOR = "vector"
    GRAPH = "graph"
    FULLTEXT = "fulltext"
    FUSION = "fusion"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class QueryRoute(StrEnum):
    AGENTIC = "agentic"
    FAST_PATH = "fast_path"
    BASELINE = "baseline"
    CHITCHAT = "chitchat"


class StreamEvent(StrEnum):
    ANSWER = "answer"
    ERROR = "error"
    CACHE_HIT = "cache_hit"
    HOP_DONE = "hop_done"
    SUB_QUESTION = "sub_question"
    THINKING = "thinking"
    TRIAGE = "triage"
    FINAL_CHAIN = "__final_chain__"


class GraphStreamMode(StrEnum):
    UPDATES = "updates"
    VALUES = "values"


class PromptName(StrEnum):
    ANSWER = "answer"
    CRITIC = "critic"
    ENTAILMENT = "entailment"
    EXECUTOR = "executor"
    EXTRACT = "extract"
    PLANNER = "planner"
    REGISTER_BUDGET = "register_budget"
    REGISTER_FORCE_ANSWER = "register_force_answer"
    REGISTER_INIT = "register_init"
    REGISTER_STATE = "register_state"
    REGISTER_SYSTEM = "register_system"
    REGISTER_UPDATE = "register_update"
    REGISTER_VERIFY = "register_verify"


class RequirementStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    RESOLVED = "resolved"
    RETIRED = "retired"


class SubQuestionStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"


class IngestStatus(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"
    EMPTY = "empty"


class ReviewType(StrEnum):
    EXTRACTION = "extraction"
    RESOLUTION = "resolution"
    CONFLICT = "conflict"
    SPOTCHECK = "spotcheck"
    FEEDBACK = "feedback"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    SKIP = "skip"


class MergeAction(StrEnum):
    MERGE = "merge"
    KEEP_SEPARATE = "keep_separate"
    UNCERTAIN = "uncertain"


class ResolutionMethod(StrEnum):
    EXACT_NORMALIZED = "exact_norm"
    SIMILARITY = "similarity"
    ALIAS = "alias"
