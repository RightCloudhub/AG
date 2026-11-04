"""AgenticGraphRAG public API client, with no graph or agent runtime dependencies."""

from ag_client.async_client import AsyncClient
from ag_client.client import Client
from ag_client.errors import AGError, APIError, ProtocolError, StreamInterrupted, TransportError
from ag_client.evidence import (
    Candidate,
    EvidenceResult,
    QueryAudit,
    SearchAudit,
    SearchResult,
    Snapshot,
    SourceRef,
)
from ag_client.models import KnowledgeBase, KnowledgeBasePage, QueryResult
from ag_client.requests import QueryRequest, SearchRequest
from ag_client.stream import StreamEvent

__version__ = "0.1.0"

__all__ = [
    "AGError",
    "APIError",
    "AsyncClient",
    "Candidate",
    "Client",
    "EvidenceResult",
    "KnowledgeBase",
    "KnowledgeBasePage",
    "ProtocolError",
    "QueryAudit",
    "QueryRequest",
    "QueryResult",
    "SearchAudit",
    "SearchRequest",
    "SearchResult",
    "Snapshot",
    "SourceRef",
    "StreamEvent",
    "StreamInterrupted",
    "TransportError",
]
